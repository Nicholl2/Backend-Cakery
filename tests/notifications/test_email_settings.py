import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.routes.settings import _persist_sender
from app.core.database import Base
from app.models.email_sender_settings import EmailSenderSettings
from app.schemas.email_settings import EmailSenderSettingsUpdate


@pytest.fixture
async def settings_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


def test_email_sender_settings_normalizes_address_and_name():
    data = EmailSenderSettingsUpdate(from_email="  Orders@Example.com ", from_name="  Toti Cakery  ")
    assert data.from_email == "orders@example.com"
    assert data.from_name == "Toti Cakery"


@pytest.mark.parametrize("email", ["not-an-email", "a@b", "x@y.com\r\nBcc:evil@example.com"])
def test_email_sender_settings_rejects_invalid_address(email):
    with pytest.raises(ValidationError):
        EmailSenderSettingsUpdate(from_email=email, from_name="Toti Cakery")


@pytest.mark.parametrize("name", ["", "  ", "Toti\r\nBcc:evil@example.com"])
def test_email_sender_settings_rejects_empty_or_header_injection_name(name):
    with pytest.raises(ValidationError):
        EmailSenderSettingsUpdate(from_email="orders@example.com", from_name=name)


@pytest.mark.asyncio
async def test_persist_sender_creates_then_updates_singleton(settings_db):
    first = EmailSenderSettingsUpdate(from_email="orders@example.com", from_name="Toti Cakery")
    await _persist_sender(settings_db, first, user_id=7)

    rows = (await settings_db.execute(select(EmailSenderSettings))).scalars().all()
    assert len(rows) == 1
    assert rows[0].id == 1
    assert rows[0].from_email == "orders@example.com"
    assert rows[0].updated_by == 7

    second = EmailSenderSettingsUpdate(from_email="new@example.com", from_name="Toti Order Desk")
    await _persist_sender(settings_db, second, user_id=8)

    await settings_db.refresh(rows[0])
    assert rows[0].from_email == "new@example.com"
    assert rows[0].from_name == "Toti Order Desk"
    assert rows[0].updated_by == 8
