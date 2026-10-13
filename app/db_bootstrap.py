"""Initialize an empty PostgreSQL database from the current SQLAlchemy schema."""
import argparse
import asyncio
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.database import Base, engine
from app.core.migrations import run_auto_migrations
from app import models  # noqa: F401 - register every mapped table


async def _verify_user_email_indexes(connection) -> None:
    """Require the lookup and trigram indexes with their actual PostgreSQL definitions."""
    definitions = (await connection.execute(text("""
        SELECT idx.relname AS name,
               am.amname AS access_method,
               i.indisunique AS is_unique,
               i.indisvalid AS is_valid,
               pg_get_expr(i.indexprs, i.indrelid) AS expression,
               pg_get_expr(i.indpred, i.indrelid) AS predicate,
               pg_get_indexdef(idx.oid) AS definition,
               (SELECT opc.opcname
                FROM unnest(i.indclass) AS cls(opclass_oid)
                JOIN pg_opclass opc ON opc.oid = cls.opclass_oid
                LIMIT 1) AS operator_class
        FROM pg_index i
        JOIN pg_class idx ON idx.oid = i.indexrelid
        JOIN pg_class tbl ON tbl.oid = i.indrelid
        JOIN pg_namespace ns ON ns.oid = tbl.relnamespace
        JOIN pg_am am ON am.oid = idx.relam
        WHERE ns.nspname = current_schema()
          AND tbl.relname = 'users'
          AND idx.relname IN ('ix_users_email', 'ix_users_email_trgm')
    """))).mappings().all()
    by_name = {row["name"]: row for row in definitions}
    btree = by_name.get("ix_users_email")
    if not (
        btree
        and btree["access_method"] == "btree"
        and btree["is_unique"]
        and btree["is_valid"]
        and btree["predicate"]
        and btree["predicate"].replace('"', "").replace(" ", "").replace("(", "").replace(")", "").lower() == "emailisnotnull"
        and "(email)" in btree["definition"].replace('"', "").replace(" ", "").lower()
    ):
        raise RuntimeError("Required partial unique B-tree index ix_users_email is missing or invalid.")

    trigram = by_name.get("ix_users_email_trgm")
    if not (
        trigram
        and trigram["access_method"] == "gin"
        and not trigram["is_unique"]
        and trigram["is_valid"]
        and trigram["predicate"] is None
        and trigram["operator_class"] == "gin_trgm_ops"
        and "(emailgin_trgm_ops)" in trigram["definition"].replace('"', "").replace(" ", "").lower()
    ):
        raise RuntimeError("Required GIN trigram index ix_users_email_trgm is missing or invalid.")


async def create_empty_database_schema(target_engine: AsyncEngine) -> None:
    if target_engine.dialect.name != "postgresql":
        raise RuntimeError("Database bootstrap only supports PostgreSQL")

    async with target_engine.begin() as connection:
        existing_tables = await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).get_table_names()
        )
        if existing_tables:
            raise RuntimeError(
                "Database is not empty; bootstrap stopped without changing it. "
                "Use the existing-database upgrade procedure instead."
            )

        existing_types = (await connection.execute(text(
            "SELECT typname FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace "
            "WHERE n.nspname = current_schema() AND t.typtype = 'e'"
        ))).scalars().all()
        if existing_types:
            raise RuntimeError(
                f"Database contains existing custom enum types ({', '.join(existing_types)}). "
                "Bootstrap stopped without changing it. Use the existing-database upgrade procedure instead."
            )

        # Check pg_trgm extension status and permission
        has_pg_trgm = (await connection.scalar(text(
            "SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'"
        ))) is not None
        if not has_pg_trgm:
            try:
                await connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm;"))
            except Exception as ext_err:
                raise RuntimeError(
                    "Administrative action required: The database user lacks permission to create the 'pg_trgm' extension. "
                    "Please run 'CREATE EXTENSION IF NOT EXISTS pg_trgm;' as a PostgreSQL superuser or database administrator "
                    f"before running database bootstrap. (Detail: {ext_err})"
                ) from ext_err

        await connection.run_sync(Base.metadata.create_all)
        await run_auto_migrations(connection)
        await _verify_user_email_indexes(connection)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create the current Toti Cakery schema in an empty PostgreSQL database"
    )
    parser.add_argument(
        "--confirm-empty", action="store_true",
        help="confirm DATABASE_URL points to a newly created, empty database",
    )
    args = parser.parse_args()
    if not args.confirm_empty:
        parser.error("--confirm-empty is required; existing databases are never bootstrapped")

    asyncio.run(create_empty_database_schema(engine))
    asyncio.run(engine.dispose())

    from alembic import command
    from alembic.config import Config

    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.stamp(config, "head")
    print("Empty PostgreSQL schema created and stamped at Alembic head.")


if __name__ == "__main__":
    main()
