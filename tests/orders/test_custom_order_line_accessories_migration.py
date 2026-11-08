from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20261108_01_custom_order_line_accessories.py"
)
SPEC = spec_from_file_location("custom_order_line_accessories_migration", MIGRATION_PATH)
MIGRATION = module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATION)


def test_migration_adds_optional_order_item_link_and_can_downgrade():
    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    orders = sa.Table("orders", metadata, sa.Column("id", sa.Integer, primary_key=True))
    items = sa.Table(
        "order_items",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("order_id", sa.Integer, sa.ForeignKey("orders.id"), nullable=False),
    )
    stocks = sa.Table("stock_items", metadata, sa.Column("id", sa.Integer, primary_key=True))
    accessories = sa.Table(
        "order_accessories",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("order_id", sa.Integer, sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("stock_item_id", sa.Integer, sa.ForeignKey("stock_items.id"), nullable=False),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            MIGRATION.upgrade()
            MIGRATION.upgrade()

        column = next(
            column for column in sa.inspect(connection).get_columns("order_accessories")
            if column["name"] == "order_item_id"
        )
        assert column["nullable"] is True
        foreign_keys = sa.inspect(connection).get_foreign_keys("order_accessories")
        assert any(
            fk["constrained_columns"] == ["order_item_id"]
            and fk["referred_table"] == "order_items"
            for fk in foreign_keys
        )
        assert "ix_order_accessories_order_item_id" in {
            index["name"] for index in sa.inspect(connection).get_indexes("order_accessories")
        }

        with Operations.context(context):
            MIGRATION.downgrade()
        assert "order_item_id" not in {
            column["name"] for column in sa.inspect(connection).get_columns("order_accessories")
        }

    engine.dispose()
