# PostgreSQL schema drift reconciliation

`app.db_reconcile` is a read-only audit for the one known mixed-schema case
where the database has refund transactions and the 20261015 accessory/snapshot
objects, but no Alembic history, no Midtrans order ID column, and no outbox
objects. It is not an automatic repair tool. Its `apply` command is deliberately
disabled until the real database shape and migration/data semantics have been
verified by a DBA against an isolated restore.

## CLI contract

Run the audit with the application environment configured for the intended
database:

```sh
python -m app.db_reconcile audit
```

This opens a PostgreSQL `READ ONLY` transaction for structural and data
checkpoints, then runs the existing baseline preflight, which performs only
catalog and data reads. Exit code `0` means the targeted checks found no
blocker and still requires DBA review. Exit code `1` means checks are blocked
or incomplete. Neither result authorizes stamping or DDL.

The explicit write-shaped interface exists to make refusal visible, but is
disabled before any database connection:

```sh
python -m app.db_reconcile apply --confirm-database toti --confirm-apply
```

It returns `APPLY_DISABLED` and exit code `2`. An audit pass does not authorize
stamping or DDL; that authorization status is intentionally never emitted.

## What the audit checks

- No existing `public.alembic_version` or conflicting outbox, audit, lease, or
  Midtrans index objects.
- The full refund table columns, types, nullability, defaults, enum labels and
  order, primary/foreign keys, unique gateway reference, and named indexes.
- Both directions of refund consistency: every refunded payment has completed
  refund rows totaling its amount, and completed refunds do not belong to a
  non-refunded payment.
- Payment columns against the current model (except the intentionally absent
  `pg_order_id`), defaults, enums, primary/foreign keys, and indexes.
- The complete `order_accessories` table shape and the two indexes created by
  `20261015_01`, plus the three snapshot columns and null checkpoints.
- Row counts for payments, refunds, stock movements, order items, and
  accessories, as well as the existing baseline preflight's stock ledger and
  data checks.

An unexpected or partial object blocks the audit. A DBA must resolve that
state through a separately reviewed plan; this tool does not repair it.

This is a targeted audit for the documented mixed-schema case, not a complete
schema fingerprint of every baseline table. It delegates baseline stock and
refund checks to `app.db_preflight`, which checks selected required columns and
the stock ledger, while this module deeply checks payments, refunds, snapshots,
and accessories. Unknown renamed/orphan objects outside the recognized
outbox-related names, and unrelated constraints or indexes on other core
tables, are not proven absent. Revision history is fail-closed: any
`public.alembic_version` object blocks the candidate state instead of trying
to infer whether its revision matches the schema. The preflight uses its own
read-only-by-query connection after the catalog transaction closes, so its
observations may be from a later snapshot if the database is changing.

## DBA review procedure before any production change

1. Confirm the production backup is usable by restoring it to a new isolated
   database. Inspect `pg_restore --list`, restore it, and verify application
   connectivity, table/enum/index/constraint definitions, and row counts. A
   successful dump command alone is not a verified backup.
2. Run `audit` against the isolated restore and save the JSON output. Compare
   its row counts and catalog details with independently captured read-only
   production results. Do not expose customer or payment values in logs.
3. Resolve every finding. If schema definitions or refund semantics differ
   from the audited fixture, stop for manual data/schema reconciliation; do not
   stamp a revision based only on object names.
4. Agree a reviewed, database-specific change plan that preserves all rows.
   It must add `payments.pg_order_id` as nullable `VARCHAR(100)` with no
   inferred/backfilled values and create its unique index only after verifying
   no conflicting object or duplicate non-NULL values.
5. Use only actual Alembic revisions whose complete schema and data semantics
   have been verified. The candidate chain is `20261008_01` → `20261013_01` →
   `20261014_01` → `20261014_02` → `20261014_03` → `20261015_01`; however,
   the current mixed schema already contains the final revision's objects, so
   blindly upgrading to head will collide. A DBA must decide and review how to
   record/adopt those exact revisions without replaying their DDL. Never use
   `stamp head`, invent a revision, or run `run_auto_migrations()` on this
   legacy database.
6. Before and after each approved stage, capture read-only revision state,
   object definitions, row counts, payment/refund consistency, and stock ledger
   preflight. Abort at the first difference from the reviewed checkpoint.
7. If a stage fails, do not automatically rerun it. PostgreSQL transactional
   DDL often rolls back with the migration transaction, but this must be
   confirmed for the exact operation and Alembic transaction settings. Inspect
   `alembic_version` and all objects read-only; resume only from the actual
   verified state. Restore the isolated backup if state cannot be proven.

The application does not currently provide the approved apply stages or
automatic recovery. A dedicated reconciler should only be added after the
restore has proven the real schema and a DBA has approved exact stage
boundaries, expected checkpoints, and recovery behavior.

## Disposable integration tests

The PostgreSQL integration module resets the `public` schema. It refuses to run
unless `RECONCILE_TEST_DATABASE_URL` points to a local PostgreSQL database with
a name ending in `_reconcile_test`. Create a fresh disposable database for the
test, then run:

```sh
RECONCILE_TEST_DB_ALLOW_RESET=1 \
RECONCILE_TEST_DATABASE_URL='postgresql+asyncpg://USER:PASS@localhost:PORT/toti_reconcile_test' \
  venv/bin/python -m pytest -q tests/integration/test_db_reconcile_audit.py
```

Never point this variable at development, staging, or production data.
