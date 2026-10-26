from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.core.config import Settings, settings
from app.main import app, lifespan


def _mock_database_startup(monkeypatch):
    import app.main as main

    @asynccontextmanager
    async def connect():
        yield SimpleNamespace(execute=_execute)

    async def _execute(*_args, **_kwargs):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: object()))

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def execute(self, *_args, **_kwargs):
            return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: object()))

    monkeypatch.setattr(main, "engine", SimpleNamespace(connect=connect))
    monkeypatch.setattr("app.core.database.AsyncSessionLocal", Session)


def test_email_worker_flag_defaults_to_disabled():
    cfg = Settings(database_url="sqlite+aiosqlite://", _env_file=None)
    assert cfg.email_worker_enabled is False


@pytest.mark.asyncio
async def test_enabled_worker_starts_and_is_cancelled_on_shutdown(monkeypatch):
    _mock_database_startup(monkeypatch)
    import app.main as main

    started = __import__("asyncio").Event()
    cancelled = __import__("asyncio").Event()

    async def fake_worker():
        started.set()
        try:
            await __import__("asyncio").Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(main.settings, "email_worker_enabled", True)
    monkeypatch.setattr("app.services.email_worker.run_email_worker", fake_worker)

    async with lifespan(app):
        await started.wait()
        assert not cancelled.is_set()

    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_disabled_worker_is_not_started(monkeypatch):
    _mock_database_startup(monkeypatch)
    import app.main as main

    started = False

    async def fake_worker():
        nonlocal started
        started = True

    monkeypatch.setattr(main.settings, "email_worker_enabled", False)
    monkeypatch.setattr("app.services.email_worker.run_email_worker", fake_worker)

    async with lifespan(app):
        pass

    assert started is False
