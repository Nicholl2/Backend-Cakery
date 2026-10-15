"""Read-only audit for the known PostgreSQL schema-drift adoption case.

Automatic application is intentionally disabled.  The supported migrations
are non-idempotent at revision 20261015_01, while the existing production
schema has no Alembic history and may contain only part of that revision.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import DateTime, Enum as SAEnum, Integer, Numeric, String, Text, inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.database import engine
from app.db_preflight import run_preflight_checks
from app.models.payment import Payment


_EXPECTED_PRE_15_PREFLIGHT_ERRORS = {
    "Schema contains tables from revisions after 20261008_01: order_accessories; do not stamp this baseline.",
    "Columns from revisions after 20261008_01 already exist in 'order_items': product_name_snapshot, unit_price_snapshot; do not stamp this baseline.",
}
_REQUIRED_CORE_TABLES = {
    "users", "roles", "categories", "products", "product_images", "recipes",
    "stock_items", "stock_movements", "price_histories", "suppliers", "purchases",
    "purchase_items", "faq_items", "expenses", "customers", "orders", "order_items",
    "invoices", "payments", "reviews", "review_images", "buyers", "otp_codes",
    "notifications", "refund_transactions",
}
_REFUND_COLUMNS = {
    "id": ("integer", False, None, None, None),
    "payment_id": ("integer", False, None, None, None),
    "refund_amount": ("numeric", False, None, 10, 2),
    "status": ("enum:refundstatusenum", False, None, None, None),
    "reason": ("varchar", False, 500, None, None),
    "gateway_ref": ("varchar", True, 100, None, None),
    "initiated_by": ("integer", True, None, None, None),
    "initiated_source": ("varchar", False, 32, None, None),
    "created_at": ("timestamptz", False, None, None, None),
    "completed_at": ("timestamptz", True, None, None, None),
}
_ACCESSORY_COLUMNS = {
    "id": ("integer", False, None, None, None),
    "order_id": ("integer", False, None, None, None),
    "stock_item_id": ("integer", False, None, None, None),
    "stock_item_name": ("varchar", False, 100, None, None),
    "unit": ("varchar", False, 20, None, None),
    "quantity": ("numeric", False, None, 10, 4),
    "unit_cost_snapshot": ("numeric", False, None, 10, 4),
    "subtotal": ("numeric", False, None, 10, 2),
}


@dataclass
class AuditReport:
    database: str | None = None
    schema: str | None = None
    result: str = "AUDIT_BLOCKED"
    checkpoints: dict[str, int | str | None] = field(default_factory=dict)
    findings: list[str] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    standard_preflight_errors: list[str] = field(default_factory=list)


def _normalize_default(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    normalized = re.sub(r"::[a-zA-Z_][a-zA-Z_0-9 .\[\]]*", "", normalized)
    normalized = normalized.strip()
    if normalized == "now()":
        return "now()"
    if re.fullmatch(r"nextval\s*\(\s*'[^']+'\s*\)", normalized):
        return "nextval"
    if len(normalized) >= 2 and normalized[0] == normalized[-1] and normalized[0] in {"'", '"'}:
        normalized = normalized[1:-1]
    return normalized


def _matches_type(column: dict[str, Any], signature: tuple) -> bool:
    kind, _nullable, length, precision, scale = signature
    column_type = column["type"]
    if kind == "integer":
        return isinstance(column_type, Integer)
    if kind == "varchar":
        return isinstance(column_type, String) and not isinstance(column_type, Text) and column_type.length == length
    if kind == "numeric":
        return (
            isinstance(column_type, Numeric)
            and column_type.precision == precision
            and column_type.scale == scale
        )
    if kind == "timestamptz":
        return isinstance(column_type, DateTime) and column_type.timezone is True
    if kind.startswith("enum:"):
        return isinstance(column_type, SAEnum) and column_type.name == kind.split(":", 1)[1]
    return False


def _signature_description(signature: tuple) -> str:
    kind, nullable, length, precision, scale = signature
    type_desc = (
        f"VARCHAR({length})" if kind == "varchar"
        else f"NUMERIC({precision},{scale})" if kind == "numeric"
        else "TIMESTAMPTZ" if kind == "timestamptz"
        else kind.upper()
    )
    return f"{type_desc} {'NULL' if nullable else 'NOT NULL'}"


def _column_matches(column: dict[str, Any] | None, signature: tuple) -> bool:
    if column is None:
        return False
    _kind, nullable, _length, _precision, _scale = signature
    return bool(column["nullable"] is nullable and _matches_type(column, signature))


def _check_column_set(
    table: str,
    actual: dict[str, dict[str, Any]],
    expected: dict[str, tuple],
    findings: list[str],
    *,
    exact: bool,
) -> None:
    for name, signature in expected.items():
        column = actual.get(name)
        if not _column_matches(column, signature):
            if column is None:
                findings.append(f"{table}.{name} is missing; expected {_signature_description(signature)}.")
            else:
                findings.append(
                    f"{table}.{name} has unexpected type/nullability; expected "
                    f"{_signature_description(signature)}."
                )
    if exact:
        extras = sorted(set(actual) - set(expected))
        if extras:
            findings.append(f"{table} has unrecognized columns: {', '.join(extras)}.")


def _default_is(column: dict[str, Any] | None, expected: str | None) -> bool:
    if column is None:
        return False
    return _normalize_default(column.get("default")) == expected


async def _columns(connection, table: str) -> dict[str, dict[str, Any]]:
    return {
        column["name"]: column
        for column in await connection.run_sync(lambda sync: inspect(sync).get_columns(table, schema="public"))
    }


async def _index_info(connection, table: str, name: str) -> dict[str, Any] | None:
    return (await connection.execute(text("""
        SELECT idx.relkind::text AS relkind,
               am.amname AS access_method,
               i.indisunique AS is_unique,
               i.indisvalid AS is_valid,
               i.indisready AS is_ready,
               i.indislive AS is_live,
               i.indisprimary AS is_primary,
               i.indpred IS NULL AS no_predicate,
               i.indexprs IS NULL AS no_expressions,
               NOT EXISTS (
                   SELECT 1 FROM unnest(i.indclass) opclass_oid
                   JOIN pg_opclass opc ON opc.oid=opclass_oid
                   WHERE NOT opc.opcdefault OR opc.opcmethod <> idx.relam
               ) AS default_opclasses,
               NOT EXISTS (
                   SELECT 1
                   FROM unnest(i.indkey::smallint[]) WITH ORDINALITY AS k(attnum, ord)
                   JOIN unnest(i.indcollation::oid[]) WITH ORDINALITY AS c(collation_oid, ord) USING (ord)
                   JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum
                   WHERE k.ord <= i.indnkeyatts AND c.collation_oid <> a.attcollation
               ) AS column_collations,
               i.indnkeyatts AS key_count,
               i.indnatts AS total_count,
               i.indoption::int[] AS key_options,
               i.indnullsnotdistinct AS nulls_not_distinct,
               pg_get_indexdef(idx.oid) AS definition,
               pg_get_indexdef(idx.oid, 1, true) AS first_key
        FROM pg_class idx
        JOIN pg_namespace ins ON ins.oid = idx.relnamespace
        LEFT JOIN pg_index i ON i.indexrelid = idx.oid
        LEFT JOIN pg_am am ON am.oid = idx.relam
        LEFT JOIN pg_class tbl ON tbl.oid = i.indrelid
        WHERE ins.nspname = 'public'
          AND idx.relname = :name
          AND (CAST(:table_name AS text) IS NULL OR tbl.relname = :table_name)
    """), {"name": name, "table_name": table})).mappings().first()


async def _check_index(
    connection,
    table: str,
    name: str,
    columns: list[str],
    findings: list[str],
    *,
    unique: bool = False,
) -> None:
    row = await _index_info(connection, table, name)
    if row is None:
        findings.append(f"Required index public.{name} on {table} is missing.")
        return
    actual = (await connection.run_sync(
        lambda sync: {item["name"]: item for item in inspect(sync).get_indexes(table, schema="public")}
    )).get(name)
    ok = bool(
        actual
        and actual.get("column_names") == columns
        and actual.get("unique") is unique
        and row["relkind"] == "i"
        and row["access_method"] == "btree"
        and row["is_unique"] is unique
        and row["is_valid"]
        and row["is_ready"]
        and row["is_live"]
        and not row["is_primary"]
        and row["no_predicate"]
        and row["no_expressions"]
        and row["default_opclasses"]
        and row["column_collations"]
        and row["key_count"] == len(columns)
        and row["total_count"] == len(columns)
        and row["key_options"] == [0] * len(columns)
        and not row["nulls_not_distinct"]
    )
    if not ok:
        findings.append(f"Index public.{name} has an unexpected or invalid definition: {row['definition']}.")


async def _check_nonconstraint_index_set(
    connection, table: str, expected_names: set[str], findings: list[str]
) -> None:
    indexes = await connection.run_sync(lambda sync: inspect(sync).get_indexes(table, schema="public"))
    # PostgreSQL also reports indexes backing table constraints. Those are
    # validated through the constraint inspectors and are excluded here.
    actual_names = {item["name"] for item in indexes if not item.get("duplicates_constraint")}
    if actual_names != expected_names:
        findings.append(
            f"{table} standalone indexes differ from expected migration state; "
            f"missing={sorted(expected_names - actual_names)!r}, "
            f"unexpected={sorted(actual_names - expected_names)!r}."
        )


async def _check_foreign_keys(
    connection,
    table: str,
    expected: set[tuple[str, str, str | None]],
    findings: list[str],
) -> None:
    foreign_keys = await connection.run_sync(
        lambda sync: inspect(sync).get_foreign_keys(table, schema="public")
    )
    actual = {
        (
            tuple(fk.get("constrained_columns") or ()),
            (fk.get("referred_schema") or "public", fk.get("referred_table")),
            tuple(fk.get("referred_columns") or ()),
            (fk.get("options") or {}).get("ondelete", "").upper() or None,
        )
        for fk in foreign_keys
    }
    wanted = {((column,), ("public", target), ("id",), ondelete) for column, target, ondelete in expected}
    if actual != wanted:
        missing = sorted(wanted - actual)
        extra = sorted(actual - wanted)
        findings.append(
            f"{table} foreign keys differ from the expected definition; "
            f"missing={missing!r}, unexpected={extra!r}."
        )
    fk_state = (await connection.execute(text("""
        SELECT convalidated, condeferrable, condeferred,
               confupdtype::text AS confupdtype, confmatchtype::text AS confmatchtype
        FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid
        JOIN pg_namespace n ON n.oid=t.relnamespace
        WHERE n.nspname='public' AND t.relname=:table_name AND c.contype='f'
    """), {"table_name": table})).all()
    if any(
        not row.convalidated or row.condeferrable or row.condeferred
        or row.confupdtype != "a" or row.confmatchtype != "s"
        for row in fk_state
    ):
        findings.append(f"{table} has an unvalidated or deferrable foreign key; migration defaults require validated immediate FKs.")


async def _check_sequence_default(connection, table: str, column: str, findings: list[str]) -> None:
    row = (await connection.execute(text("""
        SELECT pg_get_serial_sequence(format('%I.%I', 'public', CAST(:table_name AS text)), CAST(:column_name AS text)) AS owned_sequence,
               to_regclass(pg_get_serial_sequence(format('%I.%I', 'public', CAST(:table_name AS text)), CAST(:column_name AS text)))::oid AS owned_oid,
               seqns.nspname AS sequence_schema, seq.relname AS sequence_name,
               (SELECT d.refobjid
                FROM pg_attrdef ad
                JOIN pg_attribute a ON a.attrelid=ad.adrelid AND a.attnum=ad.adnum
                JOIN pg_depend d ON d.classid='pg_attrdef'::regclass AND d.objid=ad.oid
                    AND d.refclassid='pg_class'::regclass
                JOIN pg_class seq ON seq.oid=d.refobjid AND seq.relkind='S'
                WHERE ad.adrelid=format('%I.%I', 'public', CAST(:table_name AS text))::regclass
                  AND a.attname=CAST(:column_name AS text)) AS default_oid,
               (SELECT s.seqtypid = 'int4'::regtype AND s.seqstart = 1 AND s.seqincrement = 1
                    AND s.seqmin = 1 AND s.seqmax = 2147483647 AND s.seqcache = 1 AND NOT s.seqcycle
                FROM pg_sequence s
                WHERE s.seqrelid=to_regclass(pg_get_serial_sequence(
                    format('%I.%I', 'public', CAST(:table_name AS text)), CAST(:column_name AS text)))) AS expected_sequence_settings,
               (SELECT s.seqmax FROM pg_sequence s
                 WHERE s.seqrelid=to_regclass(pg_get_serial_sequence(
                     format('%I.%I', 'public', CAST(:table_name AS text)), CAST(:column_name AS text)))) AS sequence_max
        FROM (SELECT 1) AS anchor
        LEFT JOIN pg_class seq ON seq.oid=to_regclass(pg_get_serial_sequence(
            format('%I.%I', 'public', CAST(:table_name AS text)), CAST(:column_name AS text)))
        LEFT JOIN pg_namespace seqns ON seqns.oid=seq.relnamespace
    """), {"table_name": table, "column_name": column})).mappings().one()
    if (
        not row["owned_sequence"]
        or row["owned_oid"] is None
        or row["default_oid"] != row["owned_oid"]
        or row["expected_sequence_settings"] is not True
    ):
        findings.append(
            f"{table}.{column} does not use its owned integer serial sequence with migration-default settings."
        )
        return

    identifier_preparer = connection.dialect.identifier_preparer
    sequence_relation = (
        f"{identifier_preparer.quote_schema(row['sequence_schema'])}."
        f"{identifier_preparer.quote(row['sequence_name'])}"
    )
    table_relation = f"{identifier_preparer.quote_schema('public')}.{identifier_preparer.quote(table)}"
    column_identifier = identifier_preparer.quote(column)
    sequence_state = (await connection.execute(
        text(f"SELECT last_value, is_called FROM {sequence_relation}")
    )).one()
    maximum_id = await connection.scalar(text(f"SELECT max({column_identifier}) FROM {table_relation}"))
    next_sequence_value = sequence_state.last_value + 1 if sequence_state.is_called else sequence_state.last_value
    if maximum_id is not None and next_sequence_value <= maximum_id:
        findings.append(
            f"{table}.{column} sequence would next produce {next_sequence_value}, which is not above the current maximum {maximum_id}."
        )
    if next_sequence_value > row["sequence_max"]:
        findings.append(f"{table}.{column} serial sequence has no remaining integer values.")


async def _check_primary_unique_constraint_state(connection, table: str, findings: list[str]) -> None:
    states = (await connection.execute(text("""
        SELECT convalidated, condeferrable, condeferred
        FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid
        JOIN pg_namespace n ON n.oid=t.relnamespace
        WHERE n.nspname='public' AND t.relname=:table_name AND c.contype IN ('p','u')
    """), {"table_name": table})).all()
    if any(not row.convalidated or row.condeferrable or row.condeferred for row in states):
        findings.append(f"{table} has an unvalidated or deferrable primary/unique constraint.")


async def _check_payment_objects(connection, tables: set[str], findings: list[str]) -> None:
    if "payments" not in tables:
        findings.append("public.payments is missing.")
        return
    cols = await _columns(connection, "payments")
    expected_columns = {
        column.name: column
        for column in Payment.__table__.columns
        if column.name != "pg_order_id"
    }
    extras = sorted(set(cols) - set(expected_columns))
    if extras:
        findings.append(f"payments has unrecognized columns: {', '.join(extras)}.")
    for name, model_column in expected_columns.items():
        actual = cols.get(name)
        if actual is None:
            findings.append(f"payments.{name} is missing; it is required by the current payment model.")
            continue
        expected_type = model_column.type
        type_ok = (
            isinstance(actual["type"], type(expected_type))
            and (not isinstance(expected_type, String) or isinstance(expected_type, Text)
                 or actual["type"].length == expected_type.length)
            and (not isinstance(expected_type, Numeric)
                 or (actual["type"].precision == expected_type.precision
                     and actual["type"].scale == expected_type.scale))
            and (not isinstance(expected_type, DateTime)
                 or actual["type"].timezone == expected_type.timezone)
            and (not isinstance(expected_type, SAEnum)
                 or actual["type"].name == expected_type.name)
        )
        if not type_ok or actual["nullable"] is not model_column.nullable:
            findings.append(f"payments.{name} differs from the current model type or nullability.")
        if model_column.server_default:
            expected_default = _normalize_default(str(model_column.server_default.arg))
        elif model_column.primary_key and model_column.autoincrement is True:
            expected_default = "nextval"
        else:
            expected_default = None
        if _normalize_default(actual.get("default")) != expected_default:
            findings.append(f"payments.{name} has a database default different from the current model.")

    pk = await connection.run_sync(lambda sync: inspect(sync).get_pk_constraint("payments", schema="public"))
    if pk.get("constrained_columns") != ["id"]:
        findings.append("payments primary key must be (id).")
    await _check_sequence_default(connection, "payments", "id", findings)
    await _check_primary_unique_constraint_state(connection, "payments", findings)
    await _check_foreign_keys(connection, "payments", {
        ("invoice_id", "invoices", None), ("verified_by", "users", None),
    }, findings)
    for name, columns in (
        ("ix_payments_id", ["id"]),
        ("ix_payments_invoice_id", ["invoice_id"]),
    ):
        await _check_index(connection, "payments", name, columns, findings)
    await _check_nonconstraint_index_set(connection, "payments", {
        "ix_payments_id", "ix_payments_invoice_id",
    }, findings)
    if await connection.run_sync(lambda sync: inspect(sync).get_unique_constraints("payments", schema="public")):
        findings.append("payments has unrecognized unique constraints in the pre-20261014_03 state.")
    if await connection.run_sync(lambda sync: inspect(sync).get_check_constraints("payments", schema="public")):
        findings.append("payments has unrecognized CHECK constraints.")
    for enum_name, expected_labels in (
        ("paymentstatusenum", ["success", "pending", "failed", "refunded"]),
        ("paymenttypeenum", ["dp", "final"]),
    ):
        actual_labels = (await connection.execute(text("""
            SELECT e.enumlabel FROM pg_type t
            JOIN pg_namespace n ON n.oid=t.typnamespace
            JOIN pg_enum e ON e.enumtypid=t.oid
            WHERE n.nspname='public' AND t.typname=:enum_name
            ORDER BY e.enumsortorder
        """), {"enum_name": enum_name})).scalars().all()
        if actual_labels != expected_labels:
            findings.append(f"{enum_name} labels/order differ from the payment model: {actual_labels!r}.")
    if "pg_order_id" in cols:
        findings.append("payments.pg_order_id already exists; this is partial or later schema state and is not this adoption case.")
    collision = await _index_info(connection, None, "uq_payments_pg_order_id")
    if collision is not None:
        findings.append("Index name uq_payments_pg_order_id already exists; inspect its owning table/definition.")


async def _check_refund_objects(connection, tables: set[str], findings: list[str]) -> bool:
    if "refund_transactions" not in tables:
        findings.append("public.refund_transactions is missing.")
        return False
    cols = await _columns(connection, "refund_transactions")
    _check_column_set("refund_transactions", cols, _REFUND_COLUMNS, findings, exact=True)
    expected_defaults = {
        "id": "nextval",
        "status": "pending",
        "initiated_source": "seller",
        "created_at": "now()",
        "payment_id": None,
        "refund_amount": None,
        "reason": None,
        "gateway_ref": None,
        "initiated_by": None,
        "completed_at": None,
    }
    for name, expected in expected_defaults.items():
        if not _default_is(cols.get(name), expected):
            findings.append(f"refund_transactions.{name} has an unexpected database default.")

    enum_rows = (await connection.execute(text("""
        SELECT e.enumlabel
        FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
        JOIN pg_enum e ON e.enumtypid=t.oid
        WHERE n.nspname='public' AND t.typname='refundstatusenum'
        ORDER BY e.enumsortorder
    """))).scalars().all()
    if enum_rows != ["pending", "completed", "failed"]:
        findings.append(f"refundstatusenum labels/order differ from migration: {enum_rows!r}.")

    pk = await connection.run_sync(lambda sync: inspect(sync).get_pk_constraint("refund_transactions", schema="public"))
    if pk.get("constrained_columns") != ["id"]:
        findings.append("refund_transactions primary key must be (id).")
    await _check_sequence_default(connection, "refund_transactions", "id", findings)
    await _check_primary_unique_constraint_state(connection, "refund_transactions", findings)
    await _check_foreign_keys(connection, "refund_transactions", {
        ("payment_id", "payments", None), ("initiated_by", "users", None),
    }, findings)
    indexes = await connection.run_sync(lambda sync: inspect(sync).get_indexes("refund_transactions", schema="public"))
    gateway_indexes = [i for i in indexes if i.get("unique") and i.get("column_names") == ["gateway_ref"]]
    uniques = await connection.run_sync(
        lambda sync: inspect(sync).get_unique_constraints("refund_transactions", schema="public")
    )
    unique_columns = sorted(tuple(item.get("column_names") or ()) for item in uniques)
    if unique_columns != [("gateway_ref",)]:
        findings.append(
            "refund_transactions unique constraints differ from the required gateway_ref constraint: "
            f"{unique_columns!r}."
        )
    if not gateway_indexes and not any(item.get("column_names") == ["gateway_ref"] for item in uniques):
        findings.append("refund_transactions.gateway_ref must be unique, as in 20261008_01.")
    for index in gateway_indexes:
        await _check_index(connection, "refund_transactions", index["name"], ["gateway_ref"], findings, unique=True)
    await _check_index(connection, "refund_transactions", "ix_refund_transactions_id", ["id"], findings)
    await _check_index(connection, "refund_transactions", "ix_refund_transactions_payment_id", ["payment_id"], findings)
    await _check_nonconstraint_index_set(connection, "refund_transactions", {
        "ix_refund_transactions_id", "ix_refund_transactions_payment_id",
    }, findings)
    if await connection.run_sync(lambda sync: inspect(sync).get_check_constraints("refund_transactions", schema="public")):
        findings.append("refund_transactions has unrecognized CHECK constraints.")
    return not any("refund_transactions" in issue or "refundstatusenum" in issue for issue in findings)


async def _check_accessory_objects(connection, tables: set[str], findings: list[str]) -> None:
    if "order_accessories" not in tables:
        findings.append("public.order_accessories is missing; expected existing 20261015_01 drift objects.")
        return
    cols = await _columns(connection, "order_accessories")
    _check_column_set("order_accessories", cols, _ACCESSORY_COLUMNS, findings, exact=True)
    if not _default_is(cols.get("id"), "nextval"):
        findings.append("order_accessories.id must retain its serial/sequence default.")
    for column_name in set(_ACCESSORY_COLUMNS) - {"id"}:
        if not _default_is(cols.get(column_name), None):
            findings.append(f"order_accessories.{column_name} has an unexpected server default.")
    pk = await connection.run_sync(lambda sync: inspect(sync).get_pk_constraint("order_accessories", schema="public"))
    if pk.get("constrained_columns") != ["id"]:
        findings.append("order_accessories primary key must be (id).")
    await _check_sequence_default(connection, "order_accessories", "id", findings)
    await _check_primary_unique_constraint_state(connection, "order_accessories", findings)
    await _check_foreign_keys(connection, "order_accessories", {
        ("order_id", "orders", "CASCADE"), ("stock_item_id", "stock_items", None),
    }, findings)
    await _check_index(connection, "order_accessories", "ix_order_accessories_order_id", ["order_id"], findings)
    await _check_index(connection, "order_accessories", "ix_order_accessories_stock_item_id", ["stock_item_id"], findings)
    await _check_nonconstraint_index_set(connection, "order_accessories", {
        "ix_order_accessories_order_id", "ix_order_accessories_stock_item_id",
    }, findings)
    if await connection.run_sync(lambda sync: inspect(sync).get_unique_constraints("order_accessories", schema="public")):
        findings.append("order_accessories has unrecognized unique constraints.")
    if await connection.run_sync(lambda sync: inspect(sync).get_check_constraints("order_accessories", schema="public")):
        findings.append("order_accessories has unrecognized CHECK constraints.")


async def _check_snapshots(connection, tables: set[str], findings: list[str]) -> None:
    if "order_items" not in tables:
        findings.append("public.order_items is missing.")
        return
    cols = await _columns(connection, "order_items")
    expected = {
        "product_name_snapshot": ("varchar", True, 255, None, None),
        "unit_price_snapshot": ("numeric", True, None, 10, 2),
        # hpp_snapshot is model-defined, not created by 20261015_01 or run_auto_migrations.
        "hpp_snapshot": ("numeric", False, None, 10, 2),
    }
    _check_column_set("order_items", cols, expected, findings, exact=False)
    if not _default_is(cols.get("product_name_snapshot"), None):
        findings.append("order_items.product_name_snapshot must not have an unrecognized server default.")
    if not _default_is(cols.get("unit_price_snapshot"), None):
        findings.append("order_items.unit_price_snapshot must not have an unrecognized server default.")
    if not _default_is(cols.get("hpp_snapshot"), None):
        findings.append("order_items.hpp_snapshot has an unrecognized server default; review this non-Alembic drift.")


async def _check_outbox_absence(connection, tables: set[str], findings: list[str]) -> None:
    conflicts = sorted({"email_outbox", "email_outbox_audit"} & tables)
    if conflicts:
        findings.append(f"Outbox objects already exist or are partial: {', '.join(conflicts)}.")
    leftovers = (await connection.execute(text("""
        SELECT c.relname, c.relkind::text AS relkind
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public'
          AND (c.relname LIKE 'email_outbox%' OR c.relname LIKE 'email_outbox_audit%')
          AND c.relkind IN ('r','p','S','v','m','f','i','I')
        ORDER BY c.relname
    """))).mappings().all()
    if leftovers:
        findings.append(f"Outbox-related relations remain in public: {[dict(row) for row in leftovers]!r}.")
    names = (
        "ix_email_outbox_pending",
        "ix_email_outbox_lease_expiry",
        "ix_email_outbox_audit_event_id",
    )
    for name in names:
        if await _index_info(connection, None, name) is not None:
            findings.append(f"Outbox-related index name public.{name} already exists.")


async def _checkpoint(connection, tables: set[str], report: AuditReport) -> None:
    if "payments" in tables:
        report.checkpoints["payments_rows"] = await connection.scalar(text("SELECT count(*) FROM public.payments"))
        payment_columns = {
            column["name"] for column in await connection.run_sync(
                lambda sync: inspect(sync).get_columns("payments", schema="public")
            )
        }
        if "created_at" in payment_columns:
            report.checkpoints["payments_created_at_null"] = await connection.scalar(
                text("SELECT count(*) FROM public.payments WHERE created_at IS NULL")
            )
        else:
            report.checkpoints["payments_created_at_null"] = None
            report.findings.append("payments.created_at is missing; payment schema cannot be validated safely.")
    if "refund_transactions" in tables:
        report.checkpoints["refund_transactions_rows"] = await connection.scalar(
            text("SELECT count(*) FROM public.refund_transactions")
        )
        refund_columns = {
            column["name"] for column in await connection.run_sync(
                lambda sync: inspect(sync).get_columns("refund_transactions", schema="public")
            )
        }
        if (
            "payments" in tables
            and {"id", "jumlah_bayar", "payment_status"} <= payment_columns
            and {"payment_id", "refund_amount", "status"} <= refund_columns
        ):
            consistency = (await connection.execute(text("""
                WITH completed AS (
                    SELECT payment_id, sum(refund_amount) AS amount, count(*) AS rows
                    FROM public.refund_transactions
                    WHERE status = 'completed'
                    GROUP BY payment_id
                )
                SELECT
                    count(*) FILTER (WHERE lower(CAST(p.payment_status AS text)) = 'refunded') AS refunded_payments,
                    count(*) FILTER (WHERE c.rows IS NOT NULL) AS payments_with_completed_refunds,
                    count(*) FILTER (
                        WHERE lower(CAST(p.payment_status AS text)) = 'refunded'
                          AND (c.rows IS NULL OR c.amount <> p.jumlah_bayar)
                    ) AS refunded_amount_mismatches,
                    count(*) FILTER (
                        WHERE lower(CAST(p.payment_status AS text)) <> 'refunded'
                          AND c.rows IS NOT NULL
                    ) AS completed_refunds_on_non_refunded_payments
                FROM public.payments p
                LEFT JOIN completed c ON c.payment_id = p.id
            """))).mappings().one()
            for key, value in consistency.items():
                report.checkpoints[key] = value
            if consistency["refunded_amount_mismatches"]:
                report.findings.append(
                    f"{consistency['refunded_amount_mismatches']} refunded payment(s) do not have completed "
                    "refund rows totaling the payment amount."
                )
            if consistency["completed_refunds_on_non_refunded_payments"]:
                report.findings.append(
                    f"{consistency['completed_refunds_on_non_refunded_payments']} payment(s) are not refunded "
                    "but have completed refund rows."
                )
    if "stock_movements" in tables:
        report.checkpoints["stock_movements_rows"] = await connection.scalar(
            text("SELECT count(*) FROM public.stock_movements")
        )
    if "order_items" in tables:
        report.checkpoints["order_items_rows"] = await connection.scalar(text("SELECT count(*) FROM public.order_items"))
        order_item_columns = {
            column["name"] for column in await connection.run_sync(
                lambda sync: inspect(sync).get_columns("order_items", schema="public")
            )
        }
        if {"product_name_snapshot", "unit_price_snapshot", "hpp_snapshot"} <= order_item_columns:
            report.checkpoints["order_items_snapshot_nulls"] = await connection.scalar(text("""
                SELECT count(*) FROM public.order_items
                WHERE hpp_snapshot IS NULL
            """))
            if report.checkpoints["order_items_snapshot_nulls"]:
                report.findings.append("order_items.hpp_snapshot contains NULL values despite being NOT NULL in the model.")
    if "order_accessories" in tables:
        report.checkpoints["order_accessories_rows"] = await connection.scalar(
            text("SELECT count(*) FROM public.order_accessories")
        )


async def audit_database(target_engine: AsyncEngine) -> AuditReport:
    """Audit the supported mixed-drift shape without writing to the database."""
    report = AuditReport()
    if target_engine.dialect.name != "postgresql":
        report.findings.append("Only PostgreSQL is supported.")
        return report

    can_run_preflight = False
    async with target_engine.connect() as connection:
        async with connection.begin():
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            report.database = await connection.scalar(text("SELECT current_database()"))
            report.schema = await connection.scalar(text("SELECT current_schema()"))
            if report.schema != "public":
                report.findings.append(f"Active schema is {report.schema!r}; audit requires public.")
                return report
            tables = set(await connection.run_sync(lambda sync: inspect(sync).get_table_names(schema="public")))
            version_relation = (await connection.execute(text("""
                SELECT c.relkind::text AS relkind
                FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='public' AND c.relname='alembic_version'
            """))).mappings().first()
            version_exists = version_relation is not None
            if version_exists:
                if version_relation["relkind"] in {"r", "p"}:
                    version_columns = {
                        column["name"] for column in await connection.run_sync(
                            lambda sync: inspect(sync).get_columns("alembic_version", schema="public")
                        )
                    }
                    if "version_num" in version_columns:
                        versions = (await connection.execute(
                            text("SELECT version_num FROM public.alembic_version")
                        )).scalars().all()
                        report.findings.append(f"Alembic revision history already exists: {versions!r}.")
                    else:
                        report.findings.append("public.alembic_version exists without the required version_num column.")
                else:
                    report.findings.append(
                        f"public.alembic_version exists with unexpected relation kind {version_relation['relkind']!r}."
                    )
            missing_core = sorted(_REQUIRED_CORE_TABLES - tables)
            if missing_core:
                report.findings.append(f"Missing baseline tables: {', '.join(missing_core)}.")

            refund_shape_ok = await _check_refund_objects(connection, tables, report.findings)
            await _check_accessory_objects(connection, tables, report.findings)
            await _check_snapshots(connection, tables, report.findings)
            await _check_payment_objects(connection, tables, report.findings)
            await _check_outbox_absence(connection, tables, report.findings)
            await _checkpoint(connection, tables, report)

            if report.checkpoints.get("payments_created_at_null", 0):
                report.findings.append("payments.created_at contains NULL values; do not proceed with payment schema work.")

            # The existing preflight is reused for baseline, ledger, and refund-data
            # checks only after the structures it queries have been checked above.
            can_run_preflight = bool(
                not version_exists
                and not missing_core
                and refund_shape_ok
                and "order_accessories" in tables
                and "payments" in tables
                and "order_items" in tables
                and not report.findings
            )
            if not can_run_preflight:
                report.findings.append("Standard baseline preflight was not run because required structure is incomplete.")

    # The first read-only transaction has closed before the standard preflight
    # opens its own connection. This also works with a single-connection pool.
    if can_run_preflight:
        try:
            valid, errors, notices = await run_preflight_checks(target_engine, "20261008_01")
        except Exception as exc:
            report.findings.append(
                f"Baseline preflight could not complete read-only inspection: {type(exc).__name__}: {exc}"
            )
        else:
            report.notices.extend(notices)
            report.standard_preflight_errors = list(errors)
            unexpected = [error for error in errors if error not in _EXPECTED_PRE_15_PREFLIGHT_ERRORS]
            if unexpected:
                report.findings.extend(f"Baseline preflight: {error}" for error in unexpected)
            elif not valid and not errors:
                report.findings.append("Baseline preflight failed without a diagnostic.")
            elif errors:
                report.notices.append(
                    "Standard preflight rejects the known 20261015_01 objects; this audit separately validates them."
                )

    if report.findings:
        report.result = "AUDIT_BLOCKED"
    else:
        # Deliberately does not authorize changes; operator/DBA review remains required.
        report.result = "AUDIT_TARGETED_CHECKS_PASSED_DBA_REVIEW_REQUIRED"
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit the known Toti PostgreSQL schema-drift case.")
    subparsers = parser.add_subparsers(dest="mode", required=True)
    subparsers.add_parser("audit", help="run read-only structural and data checks")
    apply_parser = subparsers.add_parser("apply", help="reserved; automatic apply is not enabled")
    apply_parser.add_argument("--confirm-database", required=True, help="exact database name being targeted")
    apply_parser.add_argument("--confirm-apply", action="store_true", required=True, help="explicit operator confirmation")
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.mode == "apply":
        print(json.dumps({
            "result": "APPLY_DISABLED",
            "message": (
                "Automatic apply is disabled. The production schema has not been proven to match "
                "the synthetic fixture; use audit output for a DBA-reviewed recovery plan."
            ),
            "database_confirmation_received": bool(args.confirm_apply),
            "database_confirmation_value": args.confirm_database,
            "database_connected": False,
        }, indent=2))
        raise SystemExit(2)

    async def run_and_dispose() -> AuditReport:
        try:
            return await audit_database(engine)
        finally:
            await engine.dispose()

    report = asyncio.run(run_and_dispose())
    print(json.dumps(asdict(report), indent=2, default=str))
    raise SystemExit(0 if report.result == "AUDIT_TARGETED_CHECKS_PASSED_DBA_REVIEW_REQUIRED" else 1)


if __name__ == "__main__":
    main()
