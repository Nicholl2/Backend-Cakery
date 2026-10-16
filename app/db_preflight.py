"""Read-only preflight verification before adopting a known Alembic baseline."""
import argparse
import asyncio
import re
from decimal import Decimal, ROUND_HALF_EVEN, ROUND_HALF_UP
from pathlib import Path
from typing import Dict, List, Tuple

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.database import engine


def _normalized_index_expression(expression: str | None) -> str | None:
    """Normalize PostgreSQL's deparsed spelling of the migration expression."""
    if expression is None:
        return None
    # PostgreSQL may deparse SQL TRIM(x) as btrim(x), or retain the standard
    # TRIM(BOTH FROM x) spelling depending on server/version.
    normalized = re.sub(r'trim\s*\(\s*both\s+from\s+', "btrim(", expression.lower())
    normalized = re.sub(r'[\s"]+', "", normalized)
    return normalized.replace("btrim(", "trim(")


def _stock_name_index_is_compatible(row) -> bool:
    return bool(
        row
        and row.access_method == "btree"
        and row.is_unique
        and row.is_valid
        and row.is_ready
        and row.has_no_predicate
        and row.key_count == 1
        and row.total_count == 1
        and _normalized_index_expression(row.expression) == "lower(trim(nama_item))"
    )


def _payment_columns_after_20261008(payment_columns: set[str]) -> List[str]:
    """Return payment columns introduced after the supported 20261008 baseline."""
    return sorted({"pg_order_id"} & payment_columns)


def _validate_stock_ledger(stock, movements) -> List[str]:
    """Compare exact NUMERIC ledger quantities and replay weighted-average costs."""
    label = f"Stock item #{stock.id} '{stock.nama_item}'"
    errors: List[str] = []
    master_qty = Decimal(stock.stok_tersedia)
    master_cost = Decimal(stock.harga_per_satuan)
    qty_scale = Decimal("0.01")  # stock_items.stok_tersedia NUMERIC(10, 2)
    cost_scale = Decimal("0.0001")  # stock_items.harga_per_satuan NUMERIC(10, 4)
    if not master_qty.is_finite() or not master_cost.is_finite() or master_qty < 0 or master_cost < 0:
        errors.append(f"{label} has negative or non-finite master stock/price values.")
        return errors

    balance = Decimal("0")
    average_cost = Decimal("0")
    opening_count = 0
    opening_seen = False
    cost_types = {"PURCHASE", "ADJUSTMENT_IN"}
    signs = {
        "OPENING_BALANCE": lambda value: value > 0,
        "PURCHASE": lambda value: value > 0,
        "ADJUSTMENT_IN": lambda value: value > 0,
        "ORDER": lambda value: value < 0,
        "ADJUSTMENT_OUT": lambda value: value < 0,
        "REVERSAL": lambda value: value > 0,
    }
    for movement_index, movement in enumerate(movements):
        quantity = Decimal(movement.quantity)
        unit_cost = Decimal(movement.unit_cost)
        kind = movement.movement_type
        if not quantity.is_finite() or not unit_cost.is_finite() or unit_cost < 0:
            errors.append(f"{label} has negative or non-finite stock movement cost/quantity values.")
            continue
        if kind == "OPENING_BALANCE" and quantity < 0:
            errors.append(f"{label} has a negative OPENING_BALANCE quantity.")
            continue
        if kind not in signs or not signs[kind](quantity):
            errors.append(f"{label} has an invalid quantity sign for movement type {kind!r}.")
            continue
        if kind == "OPENING_BALANCE":
            opening_count += 1
            if opening_seen or movement_index != 0:
                errors.append(f"{label} has duplicate or out-of-order OPENING_BALANCE movements.")
                continue
            opening_seen = True
            balance = quantity.quantize(qty_scale, rounding=ROUND_HALF_UP)
            average_cost = unit_cost.quantize(cost_scale, rounding=ROUND_HALF_UP)
            continue

        new_balance = balance + quantity
        if new_balance < 0:
            errors.append(f"{label} ledger movement {movement.id} would make stock negative.")
        if kind in cost_types:
            if new_balance > 0:
                rounding = ROUND_HALF_UP if kind == "PURCHASE" else ROUND_HALF_EVEN
                average_cost = (
                    (balance * average_cost + quantity * unit_cost) / new_balance
                ).quantize(cost_scale, rounding=rounding)
            else:
                average_cost = unit_cost.quantize(cost_scale, rounding=ROUND_HALF_UP)
        balance = new_balance.quantize(qty_scale, rounding=ROUND_HALF_UP)

    if master_qty > 0 and opening_count != 1:
        if not movements or movements[0].movement_type not in {"PURCHASE", "ADJUSTMENT_IN"}:
            errors.append(
                f"{label} has positive stock but does not have exactly one OPENING_BALANCE "
                "or a valid first post-migration stock increase."
            )
    if opening_count > 1:
        errors.append(f"{label} has {opening_count} OPENING_BALANCE movements; exactly one is allowed.")
    if balance != master_qty:
        errors.append(f"{label} ledger quantity {balance} does not match master stock {master_qty}.")
    # Stored costs use NUMERIC(10, 4); a one-unit-last-place allowance covers
    # rounding of inbound unit costs before they were recorded in the ledger.
    if movements and abs(average_cost - master_cost) > cost_scale:
        errors.append(
            f"{label} reconstructed ledger unit cost {average_cost} does not match master cost {master_cost}."
        )
    return errors


async def run_preflight_checks(
    target_engine: AsyncEngine,
    target_revision: str = "20261005_01",
) -> Tuple[bool, List[str], List[str]]:
    """
    Run read-only inspection on the target database to verify all prerequisites
    required before stamping the requested Alembic baseline. Supported
    baselines are 20261005_01 (before refund objects) and 20261008_01 (after
    the refund migration, with its schema and data semantics verified).

    Returns:
        (is_valid, errors, notices)
    """
    errors: List[str] = []
    notices: List[str] = []

    if target_revision not in {"20261005_01", "20261008_01"}:
        return False, [f"Unsupported adoption revision: {target_revision}"], notices

    # 1. Verify dialect
    if target_engine.dialect.name != "postgresql":
        errors.append(f"Target dialect is '{target_engine.dialect.name}'; only PostgreSQL is supported.")
        return False, errors, notices

    async with target_engine.connect() as connection:
        # 2. Check existing Alembic revision status
        tables = set(await connection.run_sync(
            lambda sync_conn: inspect(sync_conn).get_table_names()
        ))

        if "alembic_version" in tables:
            version_rows = (await connection.execute(text(
                "SELECT version_num FROM alembic_version"
            ))).scalars().all()
            errors.append(
                f"Database already has an alembic_version table ({version_rows!r}). "
                f"Do not adopt with 'stamp {target_revision}'; inspect its revision history first."
            )

        # Revision 20261008_01 creates refund objects and backfills legacy
        # refunded payments. Never replay it over existing objects. A database
        # adopted at that revision must match its schema and data semantics.
        refund_enum_exists = await connection.scalar(text(
            "SELECT EXISTS (SELECT 1 FROM pg_type t "
            "JOIN pg_namespace n ON n.oid = t.typnamespace "
            "WHERE n.nspname = current_schema() AND t.typname = 'refundstatusenum')"
        ))
        if target_revision == "20261005_01" and ("refund_transactions" in tables or refund_enum_exists):
            errors.append(
                "Refund migration objects already exist (refund_transactions and/or refundstatusenum). "
                "Do not stamp 20261005_01 because revision 20261008_01 would be replayed; "
                "audit the objects and adopt at 20261008_01 instead."
            )

        # 3. Check core tables through 20261005_01
        required_tables = {
            "users", "roles", "categories", "products", "product_images",
            "recipes", "stock_items", "stock_movements", "price_histories",
            "suppliers", "purchases", "purchase_items", "faq_items", "expenses",
            "customers", "orders", "order_items", "invoices", "payments",
            "reviews", "review_images", "buyers", "otp_codes", "notifications",
        }
        missing_tables = sorted(required_tables - tables)
        if missing_tables:
            errors.append(
                f"Missing required tables for 20261005_01 baseline: {', '.join(missing_tables)}"
            )

        # 4. Check critical columns if tables exist
        if "orders" in tables:
            order_cols = set(await connection.run_sync(
                lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("orders")}
            ))
            required_order_cols = {"fulfillment_date", "settlement_due_date", "notes", "due_date"}
            missing_order_cols = sorted(required_order_cols - order_cols)
            if missing_order_cols:
                errors.append(f"Missing columns in 'orders': {', '.join(missing_order_cols)}")

        stock_cols: set[str] = set()
        movement_cols: set[str] = set()
        if "stock_items" in tables:
            stock_cols = set(await connection.run_sync(
                lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("stock_items")}
            ))
            required_stock_cols = {"nama_item", "harga_per_satuan", "stok_tersedia", "alert_min_stok", "version"}
            missing_stock_cols = sorted(required_stock_cols - stock_cols)
            if missing_stock_cols:
                errors.append(f"Missing columns in 'stock_items': {', '.join(missing_stock_cols)}")

        if "stock_movements" in tables:
            movement_cols = set(await connection.run_sync(
                lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("stock_movements")}
            ))
            required_movement_cols = {"stock_item_id", "movement_type", "quantity", "unit_cost"}
            missing_movement_cols = sorted(required_movement_cols - movement_cols)
            if missing_movement_cols:
                errors.append(f"Missing columns in 'stock_movements': {', '.join(missing_movement_cols)}")

        # 5. Check pg_trgm extension
        has_pg_trgm = (await connection.scalar(text(
            "SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'"
        ))) is not None
        if not has_pg_trgm:
            errors.append("Extension 'pg_trgm' is not installed in the database.")

        # 6. Check the exact unique expression index required by 20261005_01.
        if "stock_items" in tables and {"nama_item"} <= stock_cols:
            stock_name_index = (await connection.execute(text("""
                SELECT am.amname AS access_method,
                       i.indisunique AS is_unique,
                       i.indisvalid AS is_valid,
                       i.indisready AS is_ready,
                       i.indpred IS NULL AS has_no_predicate,
                       i.indnkeyatts AS key_count,
                       i.indnatts AS total_count,
                       pg_get_expr(i.indexprs, i.indrelid) AS expression
                FROM pg_index i
                JOIN pg_class idx ON idx.oid = i.indexrelid
                JOIN pg_class tbl ON tbl.oid = i.indrelid
                JOIN pg_namespace ns ON ns.oid = tbl.relnamespace
                JOIN pg_am am ON am.oid = idx.relam
                WHERE ns.nspname = current_schema()
                  AND tbl.relname = 'stock_items'
                  AND idx.relname = 'uq_stock_items_normalized_name'
            """))).mappings().first()
            if not _stock_name_index_is_compatible(stock_name_index):
                errors.append(
                    "Index 'uq_stock_items_normalized_name' is missing or does not match the required "
                    "valid UNIQUE btree expression lower(trim(nama_item)) without a predicate."
                )

        # 7. Check material duplicates: lower(trim(nama_item))
        if "stock_items" in tables and {"nama_item"} <= stock_cols:
            duplicate_rows = (await connection.execute(text(
                "SELECT lower(trim(nama_item)) AS name, count(*) AS cnt "
                "FROM stock_items GROUP BY lower(trim(nama_item)) HAVING count(*) > 1"
            ))).all()
            if duplicate_rows:
                dup_names = [f"'{r.name}' ({r.cnt}x)" for r in duplicate_rows]
                errors.append(
                    f"Duplicate stock master names must be reconciled: {', '.join(dup_names)}"
                )

        # 8. Validate opening movements and reconcile exact numeric ledger balance.
        if (
            "stock_items" in tables
            and "stock_movements" in tables
            and {"nama_item", "stok_tersedia", "harga_per_satuan"} <= stock_cols
            and {"stock_item_id", "movement_type", "quantity", "unit_cost"} <= movement_cols
        ):
            stock_rows = (await connection.execute(text(
                "SELECT id, nama_item, stok_tersedia, harga_per_satuan FROM stock_items ORDER BY id"
            ))).all()
            movements_by_stock = {}
            movement_rows = (await connection.execute(text("""
                SELECT id, stock_item_id, movement_type, quantity, unit_cost
                FROM stock_movements ORDER BY stock_item_id, created_at, id
            """))).all()
            for movement in movement_rows:
                movements_by_stock.setdefault(movement.stock_item_id, []).append(movement)
            for stock in stock_rows:
                errors.extend(_validate_stock_ledger(stock, movements_by_stock.get(stock.id, [])))

        # 9. Check legacy refund data
        if "payments" in tables:
            legacy_refund_count = await connection.scalar(text(
                "SELECT count(*) FROM payments WHERE lower(CAST(payment_status AS TEXT)) = 'refunded'"
            )) or 0
            if legacy_refund_count > 0:
                notices.append(
                    f"Found {legacy_refund_count} legacy refunded payment(s). "
                    "Alembic revision 20261008_01 must be applied after stamping 20261005_01 to migrate them to refund_transactions."
                )

        # 10. Check payment references & duplicate pg_order_id
        if "payments" in tables:
            payment_cols = set(await connection.run_sync(
                lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("payments")}
            ))
            if "created_at" not in payment_cols:
                errors.append(
                    "Missing required column 'payments.created_at'; payment schema repair "
                    "cannot backfill settled_at without it."
                )
            if "pg_order_id" in payment_cols:
                duplicate_pg_orders = (await connection.execute(text(
                    "SELECT pg_order_id, count(*) AS cnt FROM payments "
                    "WHERE pg_order_id IS NOT NULL GROUP BY pg_order_id HAVING count(*) > 1"
                ))).all()
                if duplicate_pg_orders:
                    dup_orders = [f"'{r.pg_order_id}' ({r.cnt}x)" for r in duplicate_pg_orders]
                    errors.append(
                        f"Duplicate pg_order_id values found: {', '.join(dup_orders)}. "
                        "Unique index in 20261014_03 will fail until reconciled."
                    )

            pending_count = await connection.scalar(text(
                "SELECT count(*) FROM payments WHERE lower(CAST(payment_status AS TEXT)) = 'pending'"
            )) or 0
            if pending_count > 0:
                notices.append(
                    f"Found {pending_count} pending payment(s). Pending payments must have exact Midtrans-reconciled "
                    "order IDs before status refresh or webhook processing."
                )

        if target_revision == "20261008_01":
            # These objects belong to revisions after the requested adoption
            # point. If any exist, stamping 20261008_01 would make Alembic
            # replay their non-idempotent CREATE/ADD operations.
            later_objects = {
                "email_outbox": "table",
                "email_outbox_audit": "table",
                "order_accessories": "table",
            }
            present_later_tables = sorted(name for name in later_objects if name in tables)
            if present_later_tables:
                errors.append(
                    "Schema contains tables from revisions after 20261008_01: "
                    + ", ".join(present_later_tables)
                    + "; do not stamp this baseline."
                )
            if "payments" in tables:
                payment_columns_now = await connection.run_sync(
                    lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("payments")}
                )
                # The 20261014_03 migration adds pg_order_id. Reject it here so
                # adopting at 20261008_01 cannot replay that non-idempotent add.
                later_payment_columns = _payment_columns_after_20261008(payment_columns_now)
                if later_payment_columns:
                    errors.append(
                        "Columns in 'payments' from revisions after 20261008_01 already exist: "
                        + ", ".join(later_payment_columns)
                        + "; do not stamp this baseline."
                    )
            if "order_items" in tables:
                order_item_columns = await connection.run_sync(
                    lambda sync_conn: {c["name"] for c in inspect(sync_conn).get_columns("order_items")}
                )
                later_snapshot_columns = sorted(
                    {"product_name_snapshot", "unit_price_snapshot"} & order_item_columns
                )
                if later_snapshot_columns:
                    errors.append(
                        "Columns from revisions after 20261008_01 already exist in 'order_items': "
                        + ", ".join(later_snapshot_columns)
                        + "; do not stamp this baseline."
                    )

            if "refund_transactions" not in tables or not refund_enum_exists:
                errors.append(
                    "Revision 20261008_01 requires both refund_transactions and refundstatusenum; "
                    "the existing schema does not match this adoption point."
                )
            else:
                refund_columns = await connection.run_sync(
                    lambda sync_conn: inspect(sync_conn).get_columns("refund_transactions")
                )
                columns_by_name = {column["name"]: column for column in refund_columns}
                required_refund_columns = {
                    "id", "payment_id", "refund_amount", "status", "reason", "gateway_ref",
                    "initiated_by", "initiated_source", "created_at", "completed_at",
                }
                missing_refund_columns = sorted(required_refund_columns - set(columns_by_name))
                if missing_refund_columns:
                    errors.append(
                        "Missing columns in 'refund_transactions' for 20261008_01: "
                        + ", ".join(missing_refund_columns)
                    )
                status_type = columns_by_name.get("status", {}).get("type")
                if getattr(status_type, "name", None) != "refundstatusenum":
                    errors.append("'refund_transactions.status' must use enum 'refundstatusenum'.")

                refund_status_values = (await connection.execute(text(
                    "SELECT e.enumlabel FROM pg_enum e "
                    "JOIN pg_type t ON t.oid = e.enumtypid "
                    "JOIN pg_namespace n ON n.oid = t.typnamespace "
                    "WHERE n.nspname = current_schema() AND t.typname = 'refundstatusenum' "
                    "ORDER BY e.enumsortorder"
                ))).scalars().all()
                if refund_status_values != ["pending", "completed", "failed"]:
                    errors.append(
                        "Enum 'refundstatusenum' must contain exactly pending, completed, failed "
                        f"in migration order; found {refund_status_values!r}."
                    )

                refund_indexes = await connection.run_sync(
                    lambda sync_conn: inspect(sync_conn).get_indexes("refund_transactions")
                )
                index_names = {index["name"] for index in refund_indexes}
                required_refund_indexes = {
                    "ix_refund_transactions_id", "ix_refund_transactions_payment_id",
                }
                if not required_refund_indexes <= index_names:
                    errors.append(
                        "Missing 20261008_01 refund indexes: "
                        + ", ".join(sorted(required_refund_indexes - index_names))
                    )
                if not any(
                    index.get("unique") and index.get("column_names") == ["gateway_ref"]
                    for index in refund_indexes
                ):
                    # PostgreSQL may expose a unique constraint as a constraint
                    # rather than a standalone index, so check constraints too.
                    uniques = await connection.run_sync(
                        lambda sync_conn: inspect(sync_conn).get_unique_constraints("refund_transactions")
                    )
                    if not any(unique.get("column_names") == ["gateway_ref"] for unique in uniques):
                        errors.append("'refund_transactions.gateway_ref' must have a unique constraint/index.")

                refund_fks = await connection.run_sync(
                    lambda sync_conn: inspect(sync_conn).get_foreign_keys("refund_transactions")
                )
                fk_pairs = {
                    (tuple(fk.get("constrained_columns") or ()), fk.get("referred_table"))
                    for fk in refund_fks
                }
                if (("payment_id",), "payments") not in fk_pairs or (("initiated_by",), "users") not in fk_pairs:
                    errors.append("Refund foreign keys to payments and users must match revision 20261008_01.")

                if "payments" in tables and {"id", "jumlah_bayar", "payment_status"} <= payment_cols:
                    refunded_mismatch_count = await connection.scalar(text("""
                        SELECT count(*) FROM payments p
                        WHERE lower(CAST(p.payment_status AS TEXT)) = 'refunded'
                          AND NOT EXISTS (
                              SELECT 1 FROM refund_transactions r
                              WHERE r.payment_id = p.id
                                AND r.status = 'completed'
                              GROUP BY r.payment_id
                              HAVING sum(r.refund_amount) = p.jumlah_bayar
                          )
                    """))
                    if refunded_mismatch_count:
                        errors.append(
                            f"{refunded_mismatch_count} refunded payment(s) lack completed refund rows "
                            "totalling their payment amount; reconcile before adopting 20261008_01."
                        )

    is_valid = len(errors) == 0
    return is_valid, errors, notices


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify existing PostgreSQL database prerequisites before stamping a known baseline"
    )
    parser.add_argument(
        "--target-revision", choices=("20261005_01", "20261008_01"), default="20261005_01",
        help="known schema revision to adopt (default: 20261005_01, before refund objects)",
    )
    parser.add_argument(
        "--stamp", action="store_true",
        help="if all preflight checks pass with 0 errors, stamp the selected target revision",
    )
    args = parser.parse_args()

    print(f"Running read-only preflight verification for {args.target_revision} adoption...\n")

    async def run_and_dispose():
        try:
            return await run_preflight_checks(engine, args.target_revision)
        finally:
            await engine.dispose()

    is_valid, errors, notices = asyncio.run(run_and_dispose())

    if notices:
        print("[NOTICES / MIGRATION REQUIREMENTS]")
        for note in notices:
            print(f"  * {note}")
        print()

    if not is_valid:
        print("[FAIL - PREREQUISITES NOT MET]")
        for err in errors:
            print(f"  [ERROR] {err}")
        print("\nAdoption preflight failed. Stamping stopped without modifying the database.")
        raise SystemExit(1)

    print(f"[PASS] Schema and data checks for revision {args.target_revision} passed.")

    if args.stamp:
        from alembic import command
        from alembic.config import Config

        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        command.stamp(config, args.target_revision)
        print(f"Database successfully stamped at Alembic revision {args.target_revision}.")
        print(f"Next step: Run 'alembic upgrade head' to apply revisions after {args.target_revision}.")
    else:
        print(
            f"Dry-run preflight successful. Add --stamp to adopt {args.target_revision}; "
            "otherwise this check made no database changes."
        )


if __name__ == "__main__":
    main()
