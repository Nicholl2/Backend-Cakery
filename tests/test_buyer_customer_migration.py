"""Exercise the buyer/customer migration against a disposable pre-migration DB copy."""
import importlib.util
import shutil
import sqlite3
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = ROOT / "alembic/versions/20261016_02_link_buyer_customer.py"
SPEC = importlib.util.spec_from_file_location("buyer_customer_migration", MIGRATION_PATH)
MIGRATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATION)


def test_buyer_customer_migration_updates_disposable_database_copy(tmp_path):
    source_path = tmp_path / "before_migration.sqlite"
    migrated_path = tmp_path / "migration_copy.sqlite"

    with sqlite3.connect(source_path) as source:
        source.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE buyers (
                id INTEGER PRIMARY KEY,
                phone VARCHAR(20) NOT NULL UNIQUE
            );
            CREATE TABLE customers (
                id INTEGER PRIMARY KEY,
                nama VARCHAR(100) NOT NULL,
                nomor_wa VARCHAR(20) NOT NULL UNIQUE
            );
            INSERT INTO buyers (id, phone) VALUES (1, '628123456789');
            INSERT INTO customers (id, nama, nomor_wa) VALUES
                (1, 'Linked buyer', '628123456789'),
                (2, 'Chatbot customer', '628123456780');
            """
        )
    shutil.copy2(source_path, migrated_path)

    engine = create_engine(f"sqlite:///{migrated_path}")
    with engine.begin() as target:
        target.exec_driver_sql("PRAGMA foreign_keys = ON")
        context = MigrationContext.configure(target)
        with Operations.context(context):
            MIGRATION.upgrade()

        assert target.execute(text(
            "SELECT buyer_id FROM customers WHERE id = 1"
        )).fetchone() == (1,)
        assert target.execute(text(
            "SELECT buyer_id FROM customers WHERE id = 2"
        )).fetchone() == (None,)
        index_rows = target.exec_driver_sql("PRAGMA index_list(customers)").fetchall()
        assert any(row[1] == "ix_customers_buyer_id" and row[2] == 1 for row in index_rows)
        foreign_keys = target.exec_driver_sql("PRAGMA foreign_key_list(customers)").fetchall()
        assert any(row[2] == "buyers" and row[3] == "buyer_id" and row[6].upper() == "SET NULL"
                   for row in foreign_keys)
    engine.dispose()

