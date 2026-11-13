import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.database import Base
from app.models.email_sender_settings import EmailSenderSettings
from app.utils.email_helper import send_otp_email
from app.utils.email_identity import (
    format_mailbox,
    resolve_sender_identity,
    sanitize_header_value,
)


@pytest.fixture
async def sender_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


@pytest.mark.asyncio
async def test_resolver_prefers_owner_managed_sender(sender_db, monkeypatch):
    monkeypatch.setattr(settings, "smtp_from_email", "env@example.test")
    monkeypatch.setattr(settings, "smtp_from_name", "Env Sender")
    sender_db.add(EmailSenderSettings(id=1, from_email="orders@new.example.test",
                                    from_name="Toti Order Desk"))
    await sender_db.commit()

    sender = await resolve_sender_identity(sender_db)

    assert sender.from_email == "orders@new.example.test"
    assert sender.from_name == "Toti Order Desk"


@pytest.mark.asyncio
async def test_resolver_falls_back_to_environment_per_field(sender_db, monkeypatch):
    monkeypatch.setattr(settings, "smtp_from_email", "env@example.test")
    monkeypatch.setattr(settings, "smtp_from_name", "Env Sender")
    sender_db.add(EmailSenderSettings(id=1, from_email="", from_name="Owner Name"))
    await sender_db.commit()

    sender = await resolve_sender_identity(sender_db)

    assert sender.from_email == "env@example.test"
    assert sender.from_name == "Owner Name"


def test_format_mailbox_quotes_display_name():
    header = format_mailbox('Toti "Cakery"', "Orders@Example.com")

    assert header == '"Toti \\"Cakery\\"" <orders@example.com>'


@pytest.mark.parametrize("name,address", [
    ("Toti\r\nBcc:evil@example.com", "orders@example.com"),
    ("Toti", "orders@example.com\r\nBcc:evil@example.com"),
    ("", "orders@example.com"),
])
def test_format_mailbox_rejects_header_injection(name, address):
    with pytest.raises(ValueError):
        format_mailbox(name, address)


def test_sanitize_header_value_rejects_newlines():
    with pytest.raises(ValueError):
        sanitize_header_value("buyer@example.com\r\nBcc:evil@example.com")


@pytest.mark.asyncio
async def test_otp_email_uses_owner_managed_sender(sender_db, monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.test")
    monkeypatch.setattr(settings, "smtp_port", 587)
    monkeypatch.setattr(settings, "smtp_username", "smtp-user")
    monkeypatch.setattr(settings, "smtp_password", "smtp-secret")
    monkeypatch.setattr(settings, "smtp_from_email", "env@example.test")
    monkeypatch.setattr(settings, "smtp_from_name", "Env Sender")
    monkeypatch.setattr(settings, "smtp_use_tls", True)
    sender_db.add(EmailSenderSettings(id=1, from_email="orders@new.example.test",
                                    from_name="Toti Order Desk"))
    await sender_db.commit()
    send_mock = AsyncMock()
    monkeypatch.setitem(sys.modules, "aiosmtplib", SimpleNamespace(send=send_mock))

    assert await send_otp_email("buyer@example.test", "123456", db=sender_db) is True

    message = send_mock.await_args.args[0]
    assert message["From"] == "Toti Order Desk <orders@new.example.test>"
    assert message["To"] == "buyer@example.test"
    assert "Toti Order Desk" in message["Subject"]
    assert send_mock.await_args.kwargs["timeout"] == 15


@pytest.mark.asyncio
async def test_otp_email_falls_back_to_environment_without_db(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.test")
    monkeypatch.setattr(settings, "smtp_from_email", "env@example.test")
    monkeypatch.setattr(settings, "smtp_from_name", "Env Sender")
    send_mock = AsyncMock()
    monkeypatch.setitem(sys.modules, "aiosmtplib", SimpleNamespace(send=send_mock))

    assert await send_otp_email("buyer@example.test", "123456") is True

    message = send_mock.await_args.args[0]
    assert message["From"] == "Env Sender <env@example.test>"


@pytest.mark.asyncio
async def test_otp_email_rejects_header_injection_recipient(sender_db, monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.test")
    send_mock = AsyncMock()
    monkeypatch.setitem(sys.modules, "aiosmtplib", SimpleNamespace(send=send_mock))

    ok = await send_otp_email(
        "buyer@example.test\r\nBcc:evil@example.test",
        "123456",
        db=sender_db,
    )

    assert ok is False
    send_mock.assert_not_awaited()
