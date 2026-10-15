# Deployment readiness checklist: PostgreSQL, payments, and email outbox

This checklist is for disposable local/CI databases only. Never point the integration tests or migration rehearsal at production. PostgreSQL tests require local URLs whose database names end in `_test`; fixtures may drop and recreate application tables in those explicitly dedicated test databases.

## Current audit status

- **PASS (code path), BLOCKED (PostgreSQL execution) — empty database bootstrap:** `python -m app.db_bootstrap --confirm-empty` uses the existing model/runtime schema setup, refuses databases containing tables, and stamps Alembic at head. Its PostgreSQL test was not executed here.
- **BLOCKED — PostgreSQL integration run:** no local PostgreSQL server or Docker is available in this environment. The CI workflow is wired to run the suite against three isolated PostgreSQL databases, but no CI run was triggered here.
- **BLOCKED — populated migration rehearsal:** the PostgreSQL test simulates an existing database at `20261013_01` with payment, stock movement, and outbox rows, then upgrades to head. It was not executed here. No production or staging database was accessed.
- **PASS — legacy payment policy in code:** webhook and status refresh require an exact persisted `pg_order_id`; a matching-looking legacy order ID is rejected. PostgreSQL version remains unverified here.
- **PASS — bounded worker SMTP failure exit behavior in code/tests:** a failed delivery is recorded for retry and makes `--once` exit nonzero. The scheduled GitHub Actions workflow was not run because its repository secrets are not available in this environment.

## PostgreSQL integration tests

The suites in `tests/integration/` cover:

- concurrent owner bootstrap using independent PostgreSQL sessions and the transaction advisory lock;
- concurrent outbox claim, asserting one send for one live lease;
- expired lease recovery and attempt increment;
- committed payment, invoice, and outbox state;
- rejection of a legacy pending payment without `pg_order_id` until exact reconciliation;
- batch SMTP failure, retry persistence, backlog visibility, and recovery.
- empty PostgreSQL bootstrap from current models/runtime migrations with Alembic stamped at head;
- upgrade from a recorded existing revision while preserving payment, stock movement, and outbox rows.

Run against a local disposable database whose name ends in `_test`:

```sh
export DATABASE_URL='postgresql+asyncpg://USER:PASSWORD@localhost:5432/toti_readiness_test'
export TEST_POSTGRES_URL="$DATABASE_URL"
export TEST_POSTGRES_BOOTSTRAP_URL='postgresql+asyncpg://USER:PASSWORD@localhost:5432/toti_bootstrap_test'
export TEST_POSTGRES_UPGRADE_URL='postgresql+asyncpg://USER:PASSWORD@localhost:5432/toti_upgrade_test'
export SECRET_KEY='local-test-only'
export SERVICE_API_KEY='local-test-only'
venv/bin/pytest -q tests/integration/
```

Create all three databases first. The readiness and upgrade fixtures drop/recreate application tables in their dedicated `*_test` databases; the bootstrap test requires its database to be empty. Never use a shared, staging, or production database for these commands. CI creates separate databases in its PostgreSQL 16 service and runs the integration directory explicitly.

## Migration rehearsal

1. For an empty database, run `python -m app.db_bootstrap --confirm-empty`; verify `alembic current` reports `20261014_03` and inspect the payment/outbox lease columns.
2. For an existing database with an Alembic revision, use a disposable clone and run `alembic upgrade head`; compare row counts and key financial/inventory/outbox data.
3. For an existing database with tables but no revision, do not run `upgrade head` or stamp blindly. On a clone only, run `python -m app.db_preflight` to verify schema, exact inventory indexes/ledger balances, duplicate materials, and payment references. Preflight fails closed if `refund_transactions` or `refundstatusenum` already exists; reconcile those objects manually on a verified backup before retrying. Only if preflight passes with 0 errors, execute `python -m app.db_preflight --stamp` (or `alembic stamp 20261005_01`) followed by `alembic upgrade head`. The later revisions must run so legacy refund rows and outbox tables are migrated. If verification is incomplete, stop without stamping.
4. Verify existing pending payments remain pending until exact Midtrans `transaction_id`, `order_id`, and `gross_amount` are reconciled. Backfill only an exact `order_id` obtained from a trusted Midtrans record. Otherwise resolve the payment manually; never synthesize the value from its expected format.
5. Compare row counts, payment totals/statuses, stock balances/movements, and outbox status counts before/after. Review PostgreSQL logs and lock duration, especially the unique index creation on `payments.pg_order_id`.
6. Do not downgrade as routine rollback. The `20261014_03` downgrade drops the `pg_order_id` column and its stored references. Require a verified backup and a tested restore/data-reconciliation plan before any downgrade.

## Docker email worker check

The worker is disabled by default and starts inside the API process only when `EMAIL_WORKER_ENABLED=true`. Before enabling it on the lab Docker server:

- Confirm the database migrations and SMTP configuration are ready using the procedures above and in `EMAIL_OUTBOX_OPERATIONS.md`.
- Validate the worker against a staging database and SMTP sink/test mailbox before using production SMTP.
- Set `EMAIL_WORKER_ENABLED=true` only in the lab API container environment, then recreate the container. Keep it false in tests and any deployment without a persistent process lifecycle.
- Confirm the API container is healthy and review aggregate outbox status for pending/sending/failed counts, pending and failed ages, `expired_sending`, and `oldest_expired_lease_age_seconds`.
- Keep `EMAIL_OUTBOX_ADMIN_TOKEN` restricted to authorized operator environments. Use the local CLI for event details and audited retry; never expose recipient, payload, or SMTP error through public endpoints/logs.

Do not mark the worker rollout ready until the PostgreSQL integration workflow and populated-data migration rehearsal have completed with recorded evidence, and the staging worker has been validated.
