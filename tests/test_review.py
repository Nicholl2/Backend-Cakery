import pytest
from decimal import Decimal
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool
from sqlalchemy import select

from app.core.database import Base, get_db
from app.core.security import create_access_token
from app.main import app
from app.models.buyer import Buyer
from app.models.customer import Customer
from app.models.product import Product
from app.models.order import Order, OrderItem, OrderStatusEnum, MetodePengirimanEnum
from app.models.review import Review


@pytest.fixture
async def test_env():
    test_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def override_get_db():
        async with TestSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    async with TestSessionLocal() as db:
        # Create Buyer 1 and matching Customer 1
        buyer1 = Buyer(
            name="Buyer One",
            email="buyer1@example.com",
            phone="081234567890",
            password_hash="fakehash",
            is_active=True,
        )
        # Create Buyer 2 and matching Customer 2
        buyer2 = Buyer(
            name="Buyer Two",
            email="buyer2@example.com",
            phone="089876543210",
            password_hash="fakehash",
            is_active=True,
        )
        db.add_all([buyer1, buyer2])
        await db.flush()

        customer1 = Customer(
            nama="Buyer One",
            nomor_wa="081234567890",
        )
        customer2 = Customer(
            nama="Buyer Two",
            nomor_wa="089876543210",
        )
        db.add_all([customer1, customer2])
        await db.flush()

        # Create Products
        prod1 = Product(
            nama_produk="Chocolate Cake",
            kategori="Cake",
            harga_jual=Decimal("150000.00"),
            rating=0.0,
            review_count=0,
        )
        prod2 = Product(
            nama_produk="Vanilla Cupcake",
            kategori="Cupcake",
            harga_jual=Decimal("25000.00"),
            rating=0.0,
            review_count=0,
        )
        prod3 = Product(
            nama_produk="Red Velvet Cake",
            kategori="Cake",
            harga_jual=Decimal("180000.00"),
            rating=0.0,
            review_count=0,
        )
        db.add_all([prod1, prod2, prod3])
        await db.flush()

        # Create Orders for Customer 1:
        # Order A: completed with prod1
        order_completed = Order(
            customer_id=customer1.id,
            status=OrderStatusEnum.completed,
            metode_pengiriman=MetodePengirimanEnum.delivery,
            total_harga_pesanan=Decimal("150000.00"),
        )
        # Order B: in_process with prod2
        order_pending = Order(
            customer_id=customer1.id,
            status=OrderStatusEnum.in_process,
            metode_pengiriman=MetodePengirimanEnum.pickup,
            total_harga_pesanan=Decimal("25000.00"),
        )
        # Order D: delivered with prod2
        order_delivered = Order(
            customer_id=customer1.id,
            status=OrderStatusEnum.delivered,
            metode_pengiriman=MetodePengirimanEnum.delivery,
            total_harga_pesanan=Decimal("25000.00"),
        )
        # Order E: picked_up with prod3
        order_picked_up = Order(
            customer_id=customer1.id,
            status=OrderStatusEnum.picked_up,
            metode_pengiriman=MetodePengirimanEnum.pickup,
            total_harga_pesanan=Decimal("180000.00"),
        )
        db.add_all([order_completed, order_pending, order_delivered, order_picked_up])
        await db.flush()

        item_completed = OrderItem(
            order_id=order_completed.id,
            product_id=prod1.id,
            jumlah=1,
            subtotal=Decimal("150000.00"),
        )
        item_pending = OrderItem(
            order_id=order_pending.id,
            product_id=prod2.id,
            jumlah=1,
            subtotal=Decimal("25000.00"),
        )
        item_delivered = OrderItem(
            order_id=order_delivered.id,
            product_id=prod2.id,
            jumlah=1,
            subtotal=Decimal("25000.00"),
        )
        item_picked_up = OrderItem(
            order_id=order_picked_up.id,
            product_id=prod3.id,
            jumlah=1,
            subtotal=Decimal("180000.00"),
        )
        db.add_all([item_completed, item_pending, item_delivered, item_picked_up])

        # Order C for Customer 2: completed with prod1
        order_other_customer = Order(
            customer_id=customer2.id,
            status=OrderStatusEnum.completed,
            metode_pengiriman=MetodePengirimanEnum.delivery,
            total_harga_pesanan=Decimal("150000.00"),
        )
        db.add(order_other_customer)
        await db.flush()

        item_other = OrderItem(
            order_id=order_other_customer.id,
            product_id=prod1.id,
            jumlah=1,
            subtotal=Decimal("150000.00"),
        )
        db.add(item_other)

        await db.commit()

        # IDs dictionary
        env_data = {
            "buyer1_id": buyer1.id,
            "buyer2_id": buyer2.id,
            "prod1_id": prod1.id,
            "prod2_id": prod2.id,
            "prod3_id": prod3.id,
            "order_completed_id": order_completed.id,
            "order_pending_id": order_pending.id,
            "order_delivered_id": order_delivered.id,
            "order_picked_up_id": order_picked_up.id,
            "order_other_customer_id": order_other_customer.id,
            "session_factory": TestSessionLocal,
        }

    yield env_data

    app.dependency_overrides.clear()
    await test_engine.dispose()


@pytest.mark.asyncio
async def test_create_review_success_for_completed_order(test_env):
    """Test successful review creation for a product in a completed order."""
    token = create_access_token(user_id=test_env["buyer1_id"], role_level=0, username="buyer1", role="buyer")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post(
            "/reviews/",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "order_id": test_env["order_completed_id"],
                "product_id": test_env["prod1_id"],
                "rating": 5,
                "comment": "Kue coklatnya luar biasa lezat dan lembut!",
            },
        )
        assert response.status_code == 201, response.text
        data = response.json()
        assert data["order_id"] == test_env["order_completed_id"]
        assert data["product_id"] == test_env["prod1_id"]
        assert data["rating"] == 5
        assert data["comment"] == "Kue coklatnya luar biasa lezat dan lembut!"
        assert data["komentar"] == "Kue coklatnya luar biasa lezat dan lembut!"

    # Verify Product rating & review_count updated
    session_factory = test_env["session_factory"]
    async with session_factory() as session:
        prod = await session.get(Product, test_env["prod1_id"])
        assert prod.review_count == 1
        assert prod.rating == 5.0


@pytest.mark.asyncio
async def test_create_review_delivered_and_picked_up_statuses(test_env):
    """Test review creation works for delivered and picked_up orders as completed equivalents."""
    token = create_access_token(user_id=test_env["buyer1_id"], role_level=0, username="buyer1", role="buyer")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Review for delivered order
        res1 = await ac.post(
            "/reviews/",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "order_id": test_env["order_delivered_id"],
                "product_id": test_env["prod2_id"],
                "rating": 4,
                "komentar": "Cupcake enak!",
            },
        )
        assert res1.status_code == 201
        assert res1.json()["rating"] == 4

        # Review for picked_up order
        res2 = await ac.post(
            "/reviews/",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "order_id": test_env["order_picked_up_id"],
                "product_id": test_env["prod3_id"],
                "rating": 5,
                "comment": "Red velvet mantap!",
            },
        )
        assert res2.status_code == 201
        assert res2.json()["rating"] == 5


@pytest.mark.asyncio
async def test_create_review_fails_when_order_not_completed(test_env):
    """Test review creation rejection when the order is not yet completed."""
    token = create_access_token(user_id=test_env["buyer1_id"], role_level=0, username="buyer1", role="buyer")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.post(
            "/reviews/",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "order_id": test_env["order_pending_id"],
                "product_id": test_env["prod2_id"],
                "rating": 4,
                "comment": "Pesanan masih diproses tetapi mencoba mereview",
            },
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "Hanya pesanan yang sudah selesai yang dapat diulas."


@pytest.mark.asyncio
async def test_create_review_fails_when_product_not_in_order(test_env):
    """Test review creation rejection when product was never part of the order."""
    token = create_access_token(user_id=test_env["buyer1_id"], role_level=0, username="buyer1", role="buyer")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # prod3 was not ordered in order_completed
        response = await ac.post(
            "/reviews/",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "order_id": test_env["order_completed_id"],
                "product_id": test_env["prod3_id"],
                "rating": 5,
                "comment": "Produk ini tidak ada di pesanan",
            },
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "Produk tidak terdapat dalam pesanan ini."


@pytest.mark.asyncio
async def test_create_review_duplicate_protection(test_env):
    """Test duplicate review prevention for the same (order_id, product_id)."""
    token = create_access_token(user_id=test_env["buyer1_id"], role_level=0, username="buyer1", role="buyer")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. First review succeeds
        first_resp = await ac.post(
            "/reviews/",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "order_id": test_env["order_completed_id"],
                "product_id": test_env["prod1_id"],
                "rating": 5,
                "comment": "Review pertama",
            },
        )
        assert first_resp.status_code == 201

        # 2. Second review on the exact same order and product fails
        second_resp = await ac.post(
            "/reviews/",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "order_id": test_env["order_completed_id"],
                "product_id": test_env["prod1_id"],
                "rating": 4,
                "comment": "Review kedua mencoba duplikat",
            },
        )
        assert second_resp.status_code == 400
        assert second_resp.json()["detail"] == "Anda sudah memberikan ulasan untuk produk pada pesanan ini."


@pytest.mark.asyncio
async def test_create_review_fails_for_other_customer_order(test_env):
    """Test review creation fails when order belongs to a different customer."""
    token = create_access_token(user_id=test_env["buyer1_id"], role_level=0, username="buyer1", role="buyer")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # buyer 1 attempts to review buyer 2's completed order
        response = await ac.post(
            "/reviews/",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "order_id": test_env["order_other_customer_id"],
                "product_id": test_env["prod1_id"],
                "rating": 5,
                "comment": "Mencoba review order orang lain",
            },
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "Hanya pesanan yang sudah selesai yang dapat diulas."
