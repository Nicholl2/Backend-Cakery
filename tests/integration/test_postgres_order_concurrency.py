"""PostgreSQL-only order concurrency and idempotency checks."""
import asyncio
import os
from decimal import Decimal

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.customer import Customer
from app.models.order import Order
from app.models.product import Product
from app.models.recipe import Recipe
from app.models.stock_item import KategoriEnum, SatuanEnum, StockItem
from app.services.order_service import create_new_order

pytestmark = pytest.mark.asyncio(loop_scope="function")


@pytest.fixture(scope="function")
def pg_url():
    value = os.getenv("TEST_POSTGRES_URL")
    if not value:
        pytest.skip("TEST_POSTGRES_URL is not configured; PostgreSQL order concurrency checks were not run")
    parsed = make_url(value)
    if parsed.drivername != "postgresql+asyncpg" or parsed.host not in {"localhost", "127.0.0.1"}:
        pytest.fail("TEST_POSTGRES_URL must use postgresql+asyncpg on localhost")
    if not parsed.database or not parsed.database.endswith("_test"):
        pytest.fail("TEST_POSTGRES_URL database name must end in _test; refusing to touch a non-test database")
    return value


@pytest_asyncio.fixture(scope="function", loop_scope="function")
async def pg_sessions(pg_url):
    engine = create_async_engine(pg_url, pool_pre_ping=True)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        customers = [Customer(nama=f"Concurrent buyer {n}", nomor_wa=f"+62812345000{n}") for n in (1, 2)]
        stock = StockItem(nama_item="Concurrent flour", satuan=SatuanEnum.gram,
                          kategori=KategoriEnum.bahan_baku, harga_per_satuan=1,
                          stok_tersedia=10, version=0)
        product = Product(nama_produk="Concurrent cake", harga_jual=10000, hpp_total=1000,
                          is_active=True, is_available=True, minimum_order=1,
                          slug="postgres-concurrent-cake")
        db.add_all([*customers, stock, product])
        await db.flush()
        db.add(Recipe(product_id=product.id, stock_item_id=stock.id, jumlah_dibutuhkan=10))
        await db.commit()
    try:
        yield factory
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()


async def _create(factory, customer_id: int, key: str):
    async with factory() as db:
        return await create_new_order(
            db, customer_id=customer_id,
            items=[{"product_id": 1, "jumlah": 1}],
            metode_pengiriman="pickup", email_seller_on_commit=False,
            idempotency_key=key,
        )


@pytest.mark.asyncio(loop_scope="function")
async def test_postgres_concurrent_same_key_returns_one_order(pg_sessions, monkeypatch):
    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr("app.services.notification_service.notify_order_created", noop)
    monkeypatch.setattr("app.services.chatbot_notify.notify_chatbot_order_event", noop)
    barrier = asyncio.Barrier(2)

    async def attempt():
        await barrier.wait()
        return await _create(pg_sessions, 1, "same-concurrent-key")

    first, second = await asyncio.gather(attempt(), attempt())
    assert first.id == second.id
    async with pg_sessions() as db:
        assert await db.scalar(select(func.count(Order.id))) == 1
        assert (await db.get(StockItem, 1)).stok_tersedia == Decimal("0.00")


@pytest.mark.asyncio(loop_scope="function")
async def test_postgres_concurrent_customers_cannot_oversell_locked_stock(pg_sessions, monkeypatch):
    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr("app.services.notification_service.notify_order_created", noop)
    monkeypatch.setattr("app.services.chatbot_notify.notify_chatbot_order_event", noop)
    barrier = asyncio.Barrier(2)

    async def attempt(customer_id):
        await barrier.wait()
        try:
            await _create(pg_sessions, customer_id, f"customer-key-{customer_id}")
            return "created"
        except HTTPException as error:
            return error.status_code

    outcomes = await asyncio.gather(attempt(1), attempt(2))
    assert outcomes.count("created") == 1
    assert outcomes.count(400) == 1
    async with pg_sessions() as db:
        assert await db.scalar(select(func.count(Order.id))) == 1
        assert (await db.get(StockItem, 1)).stok_tersedia == Decimal("0.00")
