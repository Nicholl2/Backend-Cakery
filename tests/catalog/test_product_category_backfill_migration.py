from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20261107_01_backfill_product_categories.py"
)
SPEC = spec_from_file_location("category_backfill_migration", MIGRATION_PATH)
MIGRATION = module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATION)


def test_backfill_links_legacy_labels_only_to_crud_categories():
    engine = sa.create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    categories = sa.Table(
        "categories",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
    )
    products = sa.Table(
        "products",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("category_id", sa.Integer, sa.ForeignKey("categories.id")),
        sa.Column("kategori", sa.String(50)),
    )
    metadata.create_all(engine)

    with engine.begin() as connection:
        connection.execute(categories.insert(), [
            {"id": 1, "name": "Cupcake"},
            {"id": 2, "name": "Kue Basah"},
        ])
        connection.execute(products.insert(), [
            {"id": 1, "kategori": "cupcake"},
            {"id": 2, "kategori": "cup cake"},
            {"id": 3, "kategori": "Cup cake"},
            {"id": 4, "kategori": "Cup Cake"},
            {"id": 5, "kategori": " kue   basah "},
            {"id": 6, "kategori": "Brownies"},
            {"id": 7, "kategori": "   "},
            {"id": 8, "kategori": None},
        ])
        connection.execute(products.insert().values(
            id=9, category_id=2, kategori="Brownies"
        ))

        migration_context = MigrationContext.configure(connection)
        with Operations.context(migration_context):
            MIGRATION.upgrade()
            MIGRATION.upgrade()  # Safe to retry without creating duplicate categories.

        category_rows = connection.execute(
            sa.select(categories.c.id, categories.c.name).order_by(categories.c.id)
        ).all()
        product_rows = connection.execute(
            sa.select(products.c.id, products.c.category_id, products.c.kategori)
            .order_by(products.c.id)
        ).all()

    assert [row.name for row in category_rows] == ["Cupcake", "Kue Basah"]
    category_ids = {row.name: row.id for row in category_rows}
    assignments = {row.id: row.category_id for row in product_rows}
    assert assignments[1] == category_ids["Cupcake"]
    assert assignments[2] == category_ids["Cupcake"]
    assert assignments[3] == category_ids["Cupcake"]
    assert assignments[4] == category_ids["Cupcake"]
    assert assignments[5] == category_ids["Kue Basah"]
    assert assignments[6] is None  # No CRUD category: leave it for manual mapping.
    assert assignments[7] is None
    assert assignments[8] is None
    assert assignments[9] == 2  # Existing explicit assignment is not overwritten.
    assert product_rows[0].kategori == "Cupcake"
    assert product_rows[1].kategori == "Cupcake"
    assert product_rows[4].kategori == "Kue Basah"

    engine.dispose()
