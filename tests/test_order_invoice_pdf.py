import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from decimal import Decimal
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.migrations import (
    ensure_product_columns,
    ensure_buyer_columns,
    ensure_stock_item_columns,
    ensure_recipe_columns,
    ensure_otp_columns,
    ensure_order_columns,
)
from app.core.seeder import seed_initial_data
from app.main import app
from app.models.role import Role
from app.models.user import User
from app.models.buyer import Buyer
from app.models.product import Product
from app.models.customer import Customer
from app.models.order import Order, OrderItem, Invoice, OrderStatusEnum, InvoiceStatusEnum, MetodePengirimanEnum
from app.models.payment import Payment, PaymentStatusEnum, PaymentTypeEnum
from app.core.security import create_access_token


@pytest.mark.asyncio
async def test_order_invoice_pdf_complete_flow():
    test_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    TestSessionLocal = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with TestSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    # 1. Run migrations & tables
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await ensure_product_columns(conn)
        await ensure_buyer_columns(conn)
        await ensure_stock_item_columns(conn)
        await ensure_recipe_columns(conn)
        await ensure_otp_columns(conn)
        await ensure_order_columns(conn)

    async with TestSessionLocal() as db:
        await seed_initial_data(db)

        # Retrieve seeded roles or create if not present
        owner_role = await db.get(Role, 1)
        admin_role = await db.get(Role, 2)
        staff_role = await db.get(Role, 3)
        if not owner_role:
            db.add(Role(id=1, nama_role="Owner", level=1))
        if not admin_role:
            db.add(Role(id=2, nama_role="Admin", level=2))
        if not staff_role:
            db.add(Role(id=3, nama_role="Staff", level=3))

        # Internal users
        admin_user = User(
            id=10,
            username="admin_test",
            email="admin@totitest.com",
            password_hash="mock",
            role_id=2,
            is_active=True,
        )
        staff_user = User(
            id=11,
            username="staff_test",
            email="staff@totitest.com",
            password_hash="mock",
            role_id=3,
            is_active=True,
        )
        owner_user = User(
            id=12,
            username="owner_test",
            email="owner@totitest.com",
            password_hash="mock",
            role_id=1,
            is_active=True,
        )
        db.add_all([admin_user, staff_user, owner_user])

        # Buyers
        buyer1 = Buyer(
            id=101,
            name="Pelanggan Satu",
            email="buyer1@test.com",
            phone="081111111111",
            password_hash="mock",
            is_verified=True,
            is_active=True,
        )
        buyer2 = Buyer(
            id=102,
            name="Pelanggan Dua",
            email="buyer2@test.com",
            phone="082222222222",
            password_hash="mock",
            is_verified=True,
            is_active=True,
        )
        db.add_all([buyer1, buyer2])

        # Customer matching buyer1 phone
        customer1 = Customer(
            id=201,
            nama="Pelanggan Satu",
            nomor_wa="081111111111",
            alamat="Jl. Mawar No. 1, Jakarta",
            is_verified=True,
        )
        db.add(customer1)
        await db.flush()

        # Product
        prod_res = await db.execute(select(Product).where(Product.is_active == True))
        product = prod_res.scalars().first()
        assert product is not None

        # Order for Buyer1
        order1 = Order(
            id=501,
            customer_id=customer1.id,
            status=OrderStatusEnum.in_process,
            metode_pengiriman=MetodePengirimanEnum.delivery,
            total_harga_pesanan=Decimal("120000.00"),
            created_via="buyer",
            notes="Tolong kirimkan lilin kecil",
            payment_method_preference="BCA VA",
        )
        db.add(order1)
        await db.flush()

        # Order Item
        item1 = OrderItem(
            order_id=order1.id,
            product_id=product.id,
            custom_product_name=None,
            jumlah=2,
            custom_decoration_charge=Decimal("10000.00"),
            subtotal=Decimal("120000.00"),
            hpp_snapshot=Decimal("30000.00"),
        )
        db.add(item1)

        # Invoice
        inv1 = Invoice(
            order_id=order1.id,
            nomor_invoice="INV-2026-00501",
            total_tagihan=Decimal("120000.00"),
            status=InvoiceStatusEnum.partial,
        )
        db.add(inv1)
        await db.flush()

        # Payment record (DP)
        payment1 = Payment(
            invoice_id=inv1.id,
            pg_transaction_id="PG-TRX-001",
            jumlah_bayar=Decimal("60000.00"),
            payment_method="bca_va",
            payment_status=PaymentStatusEnum.success,
            payment_type=PaymentTypeEnum.dp,
        )
        db.add(payment1)
        await db.commit()

    # Generate tokens
    token_buyer1 = create_access_token(user_id=101, role_level=0, username="buyer1@test.com", role="buyer")
    token_buyer2 = create_access_token(user_id=102, role_level=0, username="buyer2@test.com", role="buyer")
    token_admin = create_access_token(user_id=10, role_level=2, username="admin@totitest.com")
    token_staff = create_access_token(user_id=11, role_level=3, username="staff@totitest.com")
    token_owner = create_access_token(user_id=12, role_level=1, username="owner@totitest.com")
    token_unauth_level = create_access_token(user_id=99, role_level=4, username="guest@totitest.com")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # 1. Download PDF by Buyer1 (Owner of the order) -> Success 200
        res = await client.get(
            f"/orders/501/invoice/pdf",
            headers={"Authorization": f"Bearer {token_buyer1}"}
        )
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
        assert res.headers["content-type"] == "application/pdf"
        assert 'attachment; filename="Invoice-TotiCakery-501.pdf"' in res.headers["content-disposition"]
        assert res.content.startswith(b"%PDF-"), "Response must be a valid PDF binary"
        assert len(res.content) > 1000

        # 2. Download PDF by Staff -> Success 200
        res = await client.get(
            f"/orders/501/invoice/pdf",
            headers={"Authorization": f"Bearer {token_staff}"}
        )
        assert res.status_code == 200
        assert res.content.startswith(b"%PDF-")

        # 3. Download PDF by Admin -> Success 200
        res = await client.get(
            f"/orders/501/invoice/pdf",
            headers={"Authorization": f"Bearer {token_admin}"}
        )
        assert res.status_code == 200
        assert res.content.startswith(b"%PDF-")

        # 4. Download PDF by Owner -> Success 200
        res = await client.get(
            f"/orders/501/invoice/pdf",
            headers={"Authorization": f"Bearer {token_owner}"}
        )
        assert res.status_code == 200
        assert res.content.startswith(b"%PDF-")

        # 5. Access by Buyer2 (NOT the owner of order 501) -> 404 Not Found
        res = await client.get(
            f"/orders/501/invoice/pdf",
            headers={"Authorization": f"Bearer {token_buyer2}"}
        )
        assert res.status_code == 404, f"Buyer2 should receive 404 for order not owned, got {res.status_code}"

        # 6. Unauthenticated request -> 401 Unauthorized
        res = await client.get(f"/orders/501/invoice/pdf")
        assert res.status_code == 401

        # 7. Non-existent order -> 404 Not Found
        res = await client.get(
            f"/orders/999999/invoice/pdf",
            headers={"Authorization": f"Bearer {token_admin}"}
        )
        assert res.status_code == 404

        # 8. User with role level > 3 (unauthorized role) -> 403 Forbidden
        # First create user 99 with is_active True in DB
        async with TestSessionLocal() as db:
            db.add(User(
                id=99,
                username="guest@totitest.com",
                email="guest@totitest.com",
                password_hash="mock",
                role_id=3,
                is_active=True,
            ))
            await db.commit()

        res = await client.get(
            f"/orders/501/invoice/pdf",
            headers={"Authorization": f"Bearer {token_unauth_level}"}
        )
        assert res.status_code == 403

    app.dependency_overrides.clear()
