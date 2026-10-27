"""Repair schema objects missing from databases already stamped at head.

Revision ID: 20261027_01
Revises: 20261026_02
Create Date: 2026-10-27
"""
from alembic import op
import sqlalchemy as sa


revision = "20261027_01"
down_revision = "20261026_02"
branch_labels = None
depends_on = None


def _inspector():
    return sa.inspect(op.get_bind())


def _tables() -> set[str]:
    return set(_inspector().get_table_names())


def _columns(table_name: str) -> dict[str, dict]:
    return {column["name"]: column for column in _inspector().get_columns(table_name)}


def _ensure_primary_key(table_name: str, column_name: str, constraint_name: str) -> None:
    primary_key = _inspector().get_pk_constraint(table_name)
    if primary_key.get("constrained_columns") == [column_name]:
        return
    invalid = op.get_bind().execute(sa.text(
        f'SELECT 1 FROM "{table_name}" WHERE "{column_name}" IS NULL '
        f'OR "{column_name}" IN (SELECT "{column_name}" FROM "{table_name}" '
        f'GROUP BY "{column_name}" HAVING count(*) > 1) LIMIT 1'
    )).first()
    if invalid:
        raise RuntimeError(
            f"Cannot safely add primary key to {table_name}.{column_name}; "
            "NULL or duplicate identifiers exist"
        )
    if primary_key.get("constrained_columns"):
        raise RuntimeError(
            f"Cannot safely replace unexpected primary key on {table_name}: "
            f"{primary_key.get('constrained_columns')}"
        )
    op.create_primary_key(constraint_name, table_name, [column_name])


def _ensure_expense_columns() -> None:
    columns = _columns("expenses")
    definitions = (
        ("deskripsi", sa.String(500), True, None),
        ("penerima", sa.String(120), True, None),
        ("metode_pembayaran", sa.String(20), True, None),
        ("bukti_url", sa.String(2048), True, None),
        ("is_voided", sa.Boolean(), False, sa.false()),
        ("void_reason", sa.String(500), True, None),
        ("voided_by", sa.Integer(), True, None),
        ("voided_at", sa.DateTime(timezone=True), True, None),
        ("updated_by", sa.Integer(), True, None),
    )
    for name, type_, nullable, server_default in definitions:
        if name in columns:
            continue
        column = sa.Column(name, type_, nullable=nullable, server_default=server_default)
        if name in {"voided_by", "updated_by"}:
            column.append_foreign_key(sa.ForeignKey("users.id", ondelete="SET NULL"))
        op.add_column("expenses", column)

    # Repair databases where is_voided existed but was nullable or lacked its
    # server default. COALESCE preserves every existing non-NULL value.
    op.execute(sa.text("UPDATE expenses SET is_voided = FALSE WHERE is_voided IS NULL"))
    op.alter_column(
        "expenses", "is_voided", existing_type=sa.Boolean(), nullable=False,
        server_default=sa.false(),
    )

    inspector = _inspector()
    foreign_keys = inspector.get_foreign_keys("expenses")
    for column_name, constraint_name in (
        ("voided_by", "fk_expenses_voided_by_users"),
        ("updated_by", "fk_expenses_updated_by_users"),
    ):
        exists = any(
            fk.get("constrained_columns") == [column_name]
            and fk.get("referred_table") == "users"
            and fk.get("referred_columns") == ["id"]
            for fk in foreign_keys
        )
        if column_name in _columns("expenses") and not exists:
            op.create_foreign_key(
                constraint_name, "expenses", "users", [column_name], ["id"],
                ondelete="SET NULL",
            )

    indexes = {index["name"] for index in _inspector().get_indexes("expenses")}
    if "ix_expenses_is_voided_tanggal" not in indexes:
        op.create_index("ix_expenses_is_voided_tanggal", "expenses", ["is_voided", "tanggal"])

    checks = {item["name"] for item in _inspector().get_check_constraints("expenses")}
    if "ck_expenses_payment_method" not in checks:
        op.create_check_constraint(
            "ck_expenses_payment_method", "expenses",
            "metode_pembayaran IS NULL OR metode_pembayaran IN ('cash', 'bank_transfer', 'e_wallet', 'card', 'other')",
        )
    if "ck_expenses_void_metadata" not in checks:
        op.create_check_constraint(
            "ck_expenses_void_metadata", "expenses",
            "NOT is_voided OR (void_reason IS NOT NULL AND voided_at IS NOT NULL)",
        )


def _ensure_expense_audits() -> None:
    if "expense_audits" not in _tables():
        op.create_table(
            "expense_audits",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("expense_id", sa.Integer(), sa.ForeignKey("expenses.id", ondelete="CASCADE"), nullable=False),
            sa.Column("action", sa.String(20), nullable=False),
            sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("changes", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
    else:
        columns = _columns("expense_audits")
        if "id" not in columns:
            raise RuntimeError("Cannot safely repair expense_audits without its primary key column")
        _ensure_primary_key("expense_audits", "id", "pk_expense_audits")
        row_count = op.get_bind().execute(sa.text("SELECT count(*) FROM expense_audits")).scalar_one()
        required = {
            "expense_id": sa.Integer(),
            "action": sa.String(20),
            "actor_id": sa.Integer(),
            "changes": sa.JSON(),
            "created_at": sa.DateTime(timezone=True),
        }
        unsafe_missing = {"expense_id", "action", "changes"}.intersection(required).intersection(set(required) - set(columns))
        if row_count and unsafe_missing:
            raise RuntimeError(
                "Cannot safely infer missing required expense_audits data for existing rows: "
                + ", ".join(sorted(unsafe_missing))
            )
        for name, type_ in required.items():
            if name in columns:
                continue
            nullable = name == "actor_id"
            default = sa.func.now() if name == "created_at" else None
            op.add_column("expense_audits", sa.Column(name, type_, nullable=nullable, server_default=default))

        for name in ("expense_id", "action", "changes"):
            has_null = op.get_bind().execute(
                sa.text(f'SELECT 1 FROM expense_audits WHERE "{name}" IS NULL LIMIT 1')
            ).first()
            if has_null:
                raise RuntimeError(
                    f"expense_audits has NULL {name} values; reconcile the audit rows before migration"
                )
            op.alter_column("expense_audits", name, nullable=False)
        op.execute(sa.text("UPDATE expense_audits SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL"))
        op.alter_column(
            "expense_audits", "created_at", existing_type=sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        )

        foreign_keys = _inspector().get_foreign_keys("expense_audits")
        for column_name, target, ondelete, constraint_name in (
            ("expense_id", "expenses", "CASCADE", "fk_expense_audits_expense_id_expenses"),
            ("actor_id", "users", "SET NULL", "fk_expense_audits_actor_id_users"),
        ):
            exists = any(
                fk.get("constrained_columns") == [column_name]
                and fk.get("referred_table") == target
                and fk.get("referred_columns") == ["id"]
                for fk in foreign_keys
            )
            if not exists:
                op.create_foreign_key(constraint_name, "expense_audits", target, [column_name], ["id"], ondelete=ondelete)

    checks = {item["name"] for item in _inspector().get_check_constraints("expense_audits")}
    if "ck_expense_audits_action" not in checks:
        op.create_check_constraint(
            "ck_expense_audits_action", "expense_audits",
            "action IN ('created', 'updated', 'voided')",
        )
    indexes = {index["name"] for index in _inspector().get_indexes("expense_audits")}
    if "ix_expense_audits_expense_id" not in indexes:
        op.create_index("ix_expense_audits_expense_id", "expense_audits", ["expense_id"])


def _ensure_order_lifecycle_columns() -> None:
    columns = _columns("orders")
    for name in ("payment_expires_at", "fulfillment_overdue_notified_at"):
        if name not in columns:
            op.add_column("orders", sa.Column(name, sa.DateTime(timezone=True), nullable=True))
    indexes = {index["name"] for index in _inspector().get_indexes("orders")}
    if "ix_orders_payment_expires_at" not in indexes:
        op.create_index("ix_orders_payment_expires_at", "orders", ["payment_expires_at"])


def _ensure_email_sender_settings() -> None:
    if "email_sender_settings" not in _tables():
        op.create_table(
            "email_sender_settings",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("from_email", sa.String(255), nullable=False),
            sa.Column("from_name", sa.String(100), nullable=False),
            sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.CheckConstraint("id = 1", name="ck_email_sender_settings_singleton"),
        )
        return

    columns = _columns("email_sender_settings")
    if "id" not in columns:
        raise RuntimeError("Cannot safely repair email_sender_settings without its primary key column")
    _ensure_primary_key("email_sender_settings", "id", "pk_email_sender_settings")
    if "from_email" not in columns or "from_name" not in columns:
        count = op.get_bind().execute(sa.text("SELECT count(*) FROM email_sender_settings")).scalar_one()
        if count:
            raise RuntimeError(
                "email_sender_settings contains rows but lacks sender identity fields; "
                "provide the intended sender values before repairing this table"
            )
    definitions = (
        ("from_email", sa.String(255), False, None),
        ("from_name", sa.String(100), False, None),
        ("updated_by", sa.Integer(), True, None),
        ("updated_at", sa.DateTime(timezone=True), False, sa.func.now()),
    )
    for name, type_, nullable, default in definitions:
        if name not in columns:
            op.add_column("email_sender_settings", sa.Column(name, type_, nullable=nullable, server_default=default))
    for name in ("from_email", "from_name"):
        has_null = op.get_bind().execute(
            sa.text(f'SELECT 1 FROM email_sender_settings WHERE "{name}" IS NULL LIMIT 1')
        ).first()
        if has_null:
            raise RuntimeError(
                f"email_sender_settings has NULL {name}; provide a verified sender value before migration"
            )
        op.alter_column(
            "email_sender_settings", name,
            existing_type=sa.String(255 if name == "from_email" else 100), nullable=False,
        )
    op.execute(sa.text(
        "UPDATE email_sender_settings SET updated_at = CURRENT_TIMESTAMP WHERE updated_at IS NULL"
    ))
    op.alter_column(
        "email_sender_settings", "updated_at", existing_type=sa.DateTime(timezone=True),
        nullable=False, server_default=sa.func.now(),
    )
    if not any(
        fk.get("constrained_columns") == ["updated_by"]
        and fk.get("referred_table") == "users"
        and fk.get("referred_columns") == ["id"]
        for fk in _inspector().get_foreign_keys("email_sender_settings")
    ):
        op.create_foreign_key(
            "fk_email_sender_settings_updated_by_users", "email_sender_settings", "users",
            ["updated_by"], ["id"], ondelete="SET NULL",
        )
    checks = {item["name"] for item in _inspector().get_check_constraints("email_sender_settings")}
    if "ck_email_sender_settings_singleton" not in checks:
        invalid_ids = op.get_bind().execute(
            sa.text("SELECT id FROM email_sender_settings WHERE id <> 1 LIMIT 1")
        ).first()
        if invalid_ids:
            raise RuntimeError("email_sender_settings contains non-singleton IDs; reconcile them before migration")
        op.create_check_constraint(
            "ck_email_sender_settings_singleton", "email_sender_settings", "id = 1"
        )


def upgrade() -> None:
    tables = _tables()
    for required in ("expenses", "orders", "users"):
        if required not in tables:
            raise RuntimeError(f"Required table {required!r} is missing; refusing partial schema repair")
    _ensure_expense_columns()
    _ensure_expense_audits()
    _ensure_order_lifecycle_columns()
    _ensure_email_sender_settings()


def downgrade() -> None:
    # This is a forward-only repair migration: dropping repaired objects could
    # destroy production data when the original migration was stamped incorrectly.
    raise RuntimeError("20261027_01 is a forward-only schema repair and cannot be downgraded safely")
