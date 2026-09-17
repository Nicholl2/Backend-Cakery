import pytest
from decimal import Decimal
from datetime import datetime, timezone
import httpx
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool
from sqlalchemy import select

from app.core.database import Base, get_db
from app.core.security import create_access_token
from app.main import app
from app.models.role import Role
from app.models.user import User
from app.models.buyer import Buyer
from app.models.customer import Customer
from app.models.product import Product
from app.models.order import Order, OrderItem, Invoice, OrderStatusEnum, InvoiceStatusEnum, MetodePengirimanEnum
from app.models.payment import Payment, PaymentStatusEnum
from app.models.review import Review


@pytest.fixture
async def setup_test_app():
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

    # Seed basic roles and users
    async with TestSessionLocal() as session:
        owner_role = Role(id=1, nama_role="Owner", level=1)
        admin_role = Role(id=2, nama_role="Admin", level=2)
        staff_role = Role(id=3, nama_role="Staff", level=3)
        buyer_role = Role(id=4, nama_role="Buyer", level=4)
        session.add_all([owner_role, admin_role, staff_role, buyer_role])

        owner_user = User(
            id=1,
            username="owner@toti.com",
            email="owner@toti.com",
            password_hash="mock",
            role_id=1,
            is_active=True,
        )
        admin_user = User(
            id=2,
            username="admin@toti.com",
            email="admin@toti.com",
            password_hash="mock",
            role_id=2,
            is_active=True,
        )
        staff_user = User(
            id=3,
            username="staff@toti.com",
            email="staff@toti.com",
            password_hash="mock",
            role_id=3,
            is_active=True,
        )
        buyer_user = Buyer(
            id=1,
            name="Buyer Test",
            email="buyer@test.com",
            phone="081234567890",
            password_hash="mock",
            is_verified=True,
            is_active=True,
        )
        session.add_all([owner_user, admin_user, staff_user, buyer_user])

        # Add products
        p1 = Product(
            id=1,
            nama_produk="Kue Cokelat Fudge",
            harga_jual=Decimal("150000.00"),
            is_active=True,
            is_available=True,
        )
        p2 = Product(
            id=2,
            nama_produk="Bolu Keju Lembut",
            harga_jual=Decimal("80000.00"),
            is_active=True,
            is_available=True,
        )
        p3 = Product(
            id=3,
            nama_produk="Roti Sobek Jadul (Arsip)",
            harga_jual=Decimal("45000.00"),
            is_active=False,
            is_available=False,
        )
        session.add_all([p1, p2, p3])

        # Add customers & orders
        c1 = Customer(id=1, nama="Andi Wijaya", nomor_wa="081111111111")
        c2 = Customer(id=2, nama="Bunga Lestari", nomor_wa="082222222222")
        session.add_all([c1, c2])
        await session.flush()

        # Order 1 (Paid)
        o1 = Order(
            id=1,
            customer_id=c1.id,
            status=OrderStatusEnum.in_process,
            metode_pengiriman=MetodePengirimanEnum.delivery,
            total_harga_pesanan=Decimal("150000.00"),
            created_at=datetime(2026, 3, 1, 10, 0, 0, tzinfo=timezone.utc),
        )
        session.add(o1)
        await session.flush()

        inv1 = Invoice(
            id=1,
            order_id=o1.id,
            nomor_invoice="INV-001",
            total_tagihan=Decimal("150000.00"),
            status=InvoiceStatusEnum.paid,
        )
        session.add(inv1)
        await session.flush()

        pay1 = Payment(
            id=1,
            invoice_id=inv1.id,
            jumlah_bayar=Decimal("150000.00"),
            payment_method="TRANSFER",
            payment_status=PaymentStatusEnum.success,
            settled_at=datetime(2026, 3, 1, 10, 5, 0, tzinfo=timezone.utc),
        )
        session.add(pay1)

        # Order 2 (Pending)
        o2 = Order(
            id=2,
            customer_id=c2.id,
            status=OrderStatusEnum.pending,
            metode_pengiriman=MetodePengirimanEnum.pickup,
            total_harga_pesanan=Decimal("80000.00"),
            created_at=datetime(2026, 3, 2, 11, 0, 0, tzinfo=timezone.utc),
        )
        session.add(o2)
        await session.flush()

        inv2 = Invoice(
            id=2,
            order_id=o2.id,
            nomor_invoice="INV-002",
            total_tagihan=Decimal("80000.00"),
            status=InvoiceStatusEnum.unpaid,
        )
        session.add(inv2)

        # Add reviews
        r1 = Review(
            id=1,
            order_id=o1.id,
            product_id=p1.id,
            customer_id=c1.id,
            rating=5,
            komentar="Kue cokelatnya sangat lembut dan lezat!",
            created_at=datetime(2026, 3, 2, 14, 0, 0, tzinfo=timezone.utc),
        )
        r2 = Review(
            id=2,
            order_id=o1.id,
            product_id=p2.id,
            customer_id=c2.id,
            rating=4,
            komentar="Bolu kejunya enak, manisnya pas.",
            created_at=datetime(2026, 3, 3, 9, 0, 0, tzinfo=timezone.utc),
        )
        session.add_all([r1, r2])

        await session.commit()

    yield TestSessionLocal

    app.dependency_overrides.clear()
    await test_engine.dispose()


@pytest.mark.asyncio
async def test_dashboard_summary_contract(setup_test_app):
    """
    Test GET /reports/summary:
    - Verifies RBAC: Unauthorized (401), Buyer forbidden (403), Owner/Admin/Staff allowed (200).
    - Verifies schema: total_products, active_products, total_revenue, total_orders, recent_orders.
    """
    token_owner = create_access_token(user_id=1, role_level=1, username="owner@toti.com")
    token_admin = create_access_token(user_id=2, role_level=2, username="admin@toti.com")
    token_staff = create_access_token(user_id=3, role_level=3, username="staff@toti.com")
    token_buyer = create_access_token(user_id=1, role_level=4, username="buyer@test.com", role="buyer")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # 1. Unauthenticated request -> 401
        res_unauth = await client.get("/reports/summary")
        assert res_unauth.status_code == 401

        # 2. Buyer request -> 403
        res_buyer = await client.get("/reports/summary", headers={"Authorization": f"Bearer {token_buyer}"})
        assert res_buyer.status_code == 403

        # 3. Staff request -> 200
        res_staff = await client.get("/reports/summary", headers={"Authorization": f"Bearer {token_staff}"})
        assert res_staff.status_code == 200

        # 4. Admin request -> 200
        res_admin = await client.get("/reports/summary", headers={"Authorization": f"Bearer {token_admin}"})
        assert res_admin.status_code == 200

        # 5. Owner request -> 200 and validate payload
        res_owner = await client.get("/reports/summary", headers={"Authorization": f"Bearer {token_owner}"})
        assert res_owner.status_code == 200
        data = res_owner.json()

        assert "total_products" in data
        assert "active_products" in data
        assert "total_revenue" in data
        assert "total_orders" in data
        assert "recent_orders" in data

        assert data["total_products"] == 3
        assert data["active_products"] == 2
        assert float(data["total_revenue"]) == 150000.0
        assert data["total_orders"] == 2

        # Validate recent orders structure
        recent_orders = data["recent_orders"]
        assert len(recent_orders) == 2
        first_order = recent_orders[0]
        assert "id" in first_order
        assert "customer_name" in first_order
        assert "status" in first_order
        assert first_order["customer_name"] in ["Andi Wijaya", "Bunga Lestari"]


@pytest.mark.asyncio
async def test_manual_payment_contract(setup_test_app):
    """
    Test POST /payments/manual:
    - Accepts order_id (str), amount (float), payment_method (str), notes (str).
    - Transitions order status to in_process, invoice to paid, and payment_status to PAID.
    - Records new payment with status Success.
    """
    token_staff = create_access_token(user_id=3, role_level=3, username="staff@toti.com")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Order 2 was created with total_harga_pesanan = 80000 and status = pending
        payload = {
            "order_id": "2",
            "amount": 80000.0,
            "payment_method": "CASH",
            "notes": "Pembayaran tunai lunas di meja kasir",
        }

        res = await client.post(
            "/payments/manual",
            json=payload,
            headers={"Authorization": f"Bearer {token_staff}"}
        )
        assert res.status_code == 200, f"Error: {res.text}"
        data = res.json()

        assert data["success"] is True
        assert data["payment_status"] == "PAID"
        assert data["order_status"] == "in_process"
        assert data["order_id"] == 2
        assert float(data["amount"]) == 80000.0
        assert data["payment_method"] == "CASH"

    # Verify directly in database
    SessionLocal = setup_test_app
    async with SessionLocal() as db:
        order_res = await db.execute(select(Order).where(Order.id == 2))
        order = order_res.scalars().first()
        assert order.status == OrderStatusEnum.in_process
        assert order.payment_status == "PAID"
        assert order.invoice.status == InvoiceStatusEnum.paid

        payments_res = await db.execute(select(Payment).where(Payment.invoice_id == order.invoice.id))
        payments = payments_res.scalars().all()
        assert len(payments) == 1
        p = payments[0]
        assert p.payment_status == PaymentStatusEnum.success
        assert p.payment_method == "CASH"
        assert p.verified_by == 3


@pytest.mark.asyncio
async def test_global_reviews_latest_contract(setup_test_app):
    """
    Test GET /reviews/latest:
    - Query parameter limit (default 6).
    - Returns latest reviews globally without requiring product_id.
    - Eagerly loads product_name and customer_name to prevent lazy-loading MissingGreenlet errors.
    """
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # 1. Test default limit
        res = await client.get("/reviews/latest")
        assert res.status_code == 200
        data = res.json()
        assert isinstance(data, list)
        assert len(data) == 2

        # Verify ordering (newest first: r2 was created at 9:00 on March 3, r1 on March 2)
        assert data[0]["id"] == 2
        assert data[0]["rating"] == 4
        assert data[0]["product_name"] == "Bolu Keju Lembut"
        assert data[0]["customer_name"] == "Bunga Lestari"

        assert data[1]["id"] == 1
        assert data[1]["rating"] == 5
        assert data[1]["product_name"] == "Kue Cokelat Fudge"
        assert data[1]["customer_name"] == "Andi Wijaya"

        # 2. Test custom limit=1
        res_limit1 = await client.get("/reviews/latest?limit=1")
        assert res_limit1.status_code == 200
        data_limit1 = res_limit1.json()
        assert len(data_limit1) == 1
        assert data_limit1[0]["id"] == 2
