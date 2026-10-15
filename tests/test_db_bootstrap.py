"""Unit tests for PostgreSQL database bootstrap ordering."""
from types import SimpleNamespace

import pytest

import app.db_bootstrap as db_bootstrap


@pytest.mark.asyncio
async def test_schema_bootstrap_locks_before_inspection_in_one_transaction(monkeypatch):
    events = []
    transaction_active = False

    class Result:
        def scalars(self):
            return self

        def all(self):
            return []

    class Connection:
        async def execute(self, statement, parameters=None):
            assert transaction_active
            events.append(("execute", str(statement), parameters))
            return Result()

        async def scalar(self, statement):
            assert transaction_active
            events.append(("scalar", str(statement)))
            return 1  # pg_trgm is already installed in this fake database.

        async def run_sync(self, callback):
            assert transaction_active
            events.append(("run_sync",))
            return callback(SimpleNamespace())

    connection = Connection()

    class Transaction:
        async def __aenter__(self):
            nonlocal transaction_active
            transaction_active = True
            events.append(("begin",))
            return connection

        async def __aexit__(self, exc_type, exc, traceback):
            nonlocal transaction_active
            events.append(("rollback" if exc_type else "commit",))
            transaction_active = False

    class Inspector:
        def get_table_names(self):
            events.append(("inspect_tables",))
            return []

    monkeypatch.setattr(db_bootstrap, "inspect", lambda _: Inspector())
    monkeypatch.setattr(
        db_bootstrap.Base.metadata,
        "create_all",
        lambda _: events.append(("create_all",)),
    )

    async def record_auto_migrations(_):
        assert transaction_active
        events.append(("auto_migrations",))

    async def record_index_verification(_):
        assert transaction_active
        events.append(("verify_indexes",))

    monkeypatch.setattr(db_bootstrap, "run_auto_migrations", record_auto_migrations)
    monkeypatch.setattr(db_bootstrap, "_verify_user_email_indexes", record_index_verification)
    target_engine = SimpleNamespace(
        dialect=SimpleNamespace(name="postgresql"),
        begin=lambda: Transaction(),
    )

    await db_bootstrap.create_empty_database_schema(target_engine)

    isolation_event = next(
        event for event in events
        if event[0] == "execute" and "SET TRANSACTION ISOLATION LEVEL READ COMMITTED" in event[1]
    )
    lock_event = next(
        event for event in events
        if event[0] == "execute" and "pg_advisory_xact_lock" in event[1]
    )
    assert "pg_advisory_xact_lock" in lock_event[1]
    assert lock_event[2] == {"lock_key": db_bootstrap._SCHEMA_BOOTSTRAP_ADVISORY_LOCK_KEY}
    assert events.index(isolation_event) < events.index(lock_event)
    assert events.index(lock_event) < events.index(("inspect_tables",))
    assert events.index(("create_all",)) < events.index(("auto_migrations",))
    assert events[-1] == ("commit",)
