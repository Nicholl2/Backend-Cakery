import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asyncio
from decimal import Decimal
from unittest.mock import patch, AsyncMock
import httpx
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy import select

from app.core.database import Base, get_db
from app.core.config import settings
from app.core.security import create_access_token
from app.main import app
from app.models.role import Role
from app.models.user import User
from app.models.buyer import Buyer
from app.models.customer import Customer
from app.models.product import Product
from app.models.stock_item import StockItem, SatuanEnum, KategoriEnum
from app.models.recipe import Recipe
from app.models.order import Order, OrderItem, Invoice, OrderStatusEnum, InvoiceStatusEnum, MetodePengirimanEnum
from app.models.payment import Payment, PaymentStatusEnum, PaymentTypeEnum


async def run_chatbot_refund_tests():
    print("🚀 Starting Chatbot Webhook & Service-to-Service Refund Tests...")

    test_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = async_sessionmaker(bind=test_engine, expire_on_commit=False)

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def override_get_db():
        async with TestSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    # Configure dummy settings
    test_service_key = "test_chatbot_service_key_123"
    test_internal_key = "test_internal_chatbot_key_456"
    test_chatbot_url = "http://mock-chatbot:8000"

    settings.service_api_key = test_service_key
    settings.chatbot_internal_key = test_internal_key
    settings.chatbot_url = test_chatbot_url

    async with TestSessionLocal() as db:
        # Seed Roles & Users
        admin_role = Role(id=2, nama_role="Admin", level=2)
        db.add(admin_role)

        admin_user = User(
            id=1,
            username="admin_test",
            email="admin@test.com",
            password_hash="mockpwd",
            role_id=2,
            is_active=True,
        )
        db.add(admin_user)

        # Seed Customers
        cust1 = Customer(
            id=1,
            nama="Budi Santoso",
            nomor_wa="6281234567890",
            alamat="Jakarta",
        )
        cust2 = Customer(
            id=2,
            nama="Siti Rahma",
            nomor_wa="6289999888877",
            alamat="Bandung",
        )
        db.add_all([cust1, cust2])

        # Seed Stock & Product
        tepung = StockItem(
            id=1,
            nama_item="Tepung",
            satuan=SatuanEnum.gram,
            kategori=KategoriEnum.bahan_baku,
            harga_per_satuan=Decimal("0.02"),
            stok_tersedia=Decimal("1000.00"),
            alert_min_stok=Decimal("100.00"),
            version=0,
        )
        db.add(tepung)

        cake = Product(
            id=1,
            nama_produk="Kue Tart Strawberry",
            deskripsi="Tart lezat",
            kategori="Cake",
            harga_jual=Decimal("100000.00"),
            hpp_total=Decimal("40000.00"),
            is_active=True,
            minimum_order=1,
        )
        db.add(cake)
        await db.flush()

        recipe = Recipe(
            product_id=cake.id,
            stock_item_id=tepung.id,
            jumlah_dibutuhkan=Decimal("100.0000"),
            quantity_required=Decimal("100.0000"),
            unit="gram",
        )
        db.add(recipe)

        # Order 1: Belongs to Budi (6281234567890), Status = 'pending', Invoice = 'partial' (DP Paid)
        order1 = Order(
            id=1,
            customer_id=1,
            status=OrderStatusEnum.pending,
            metode_pengiriman=MetodePengirimanEnum.pickup,
            total_harga_pesanan=Decimal("100000.00"),
            created_via="chatbot",
        )
        db.add(order1)
        await db.flush()

        inv1 = Invoice(
            id=1,
            order_id=1,
            nomor_invoice="INV-2026-001",
            total_tagihan=Decimal("100000.00"),
            status=InvoiceStatusEnum.partial,
        )
        db.add(inv1)
        await db.flush()

        pay1 = Payment(
            id=1,
            invoice_id=1,
            pg_transaction_id="pg-trans-001",
            jumlah_bayar=Decimal("50000.00"),
            payment_method="bank_transfer",
            payment_status=PaymentStatusEnum.success,
            payment_type=PaymentTypeEnum.dp,
        )
        db.add(pay1)

        item1 = OrderItem(
            id=1,
            order_id=1,
            product_id=1,
            jumlah=1,
            subtotal=Decimal("100000.00"),
        )
        db.add(item1)

        # Order 2: Belongs to Budi (6281234567890), Status = 'in_process', Invoice = 'paid'
        order2 = Order(
            id=2,
            customer_id=1,
            status=OrderStatusEnum.in_process,
            metode_pengiriman=MetodePengirimanEnum.pickup,
            total_harga_pesanan=Decimal("100000.00"),
            created_via="chatbot",
        )
        db.add(order2)
        await db.flush()

        inv2 = Invoice(
            id=2,
            order_id=2,
            nomor_invoice="INV-2026-002",
            total_tagihan=Decimal("100000.00"),
            status=InvoiceStatusEnum.paid,
        )
        db.add(inv2)
        await db.flush()

        pay2 = Payment(
            id=2,
            invoice_id=2,
            pg_transaction_id="pg-trans-002",
            jumlah_bayar=Decimal("100000.00"),
            payment_method="bank_transfer",
            payment_status=PaymentStatusEnum.success,
            payment_type=PaymentTypeEnum.final,
        )
        db.add(pay2)

        item2 = OrderItem(
            id=2,
            order_id=2,
            product_id=1,
            jumlah=1,
            subtotal=Decimal("100000.00"),
        )
        db.add(item2)

        await db.commit()

    token_admin = create_access_token(user_id=1, role_level=2, username="admin_test")
    service_headers = {"X-Service-Key": test_service_key}
    admin_headers = {"Authorization": f"Bearer {token_admin}"}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # =====================================================================
        # 1. Chatbot Service Key: Verification Ownership Check
        # =====================================================================
        print("\n--- 1. Testing Chatbot Service-to-Service Refund Ownership Check ---")

        # (a) Missing nomor_wa -> 400 Bad Request
        res = await client.post(
            "/orders/1/refund",
            json={"reason": "Mau batalkan pesanan"},
            headers=service_headers,
        )
        assert res.status_code == 400, f"Expected 400 for missing nomor_wa, got {res.status_code}"
        print("  ✓ Missing 'nomor_wa' rejected (400 Bad Request)")

        # (b) Mismatching nomor_wa (Siti tries to refund Budi's order) -> 403 Forbidden
        res = await client.post(
            "/orders/1/refund",
            json={"reason": "Mau batalkan pesanan", "nomor_wa": "089999888877"},
            headers=service_headers,
        )
        assert res.status_code == 403, f"Expected 403 for mismatched phone, got {res.status_code}"
        print("  ✓ Mismatched phone rejected (403 Forbidden)")

        # =====================================================================
        # 2. Chatbot Service Key: Strict Status Check (Pending only)
        # =====================================================================
        print("\n--- 2. Testing Chatbot Strict Status Check (Pending only) ---")

        # (a) Order 2 is 'in_process' -> Chatbot refund REJECTED with 400 Bad Request
        res = await client.post(
            "/orders/2/refund",
            json={"reason": "Customer minta batal", "nomor_wa": "081234567890"},
            headers=service_headers,
        )
        assert res.status_code == 400, f"Expected 400 for non-pending order from chatbot, got {res.status_code}"
        assert "pending" in res.text.lower()
        print("  ✓ Chatbot refund for 'in_process' order rejected (400 Bad Request)")

        # =====================================================================
        # 3. Chatbot Service Key: Successful Refund on Pending Order + Webhook
        # =====================================================================
        print("\n--- 3. Testing Chatbot Successful Refund & Webhook Trigger ---")

        with patch("app.services.chatbot_notify.notify_chatbot_order_event", new_callable=AsyncMock) as mock_webhook:
            # Order 1 is 'pending', phone matches -> 200 OK
            res = await client.post(
                "/orders/1/refund",
                json={"reason": "Customer berubah pikiran sebelum diproses", "nomor_wa": "081234567890"},
                headers=service_headers,
            )
            assert res.status_code == 200, f"Expected 200 for chatbot refund on pending order, got {res.status_code} - {res.text}"
            order_resp = res.json()
            assert order_resp["status"] == "cancelled"
            print(f"  ✓ Chatbot successfully refunded order 1 (status={order_resp['status']})")

            # Check that refunded webhook was called for order 1
            mock_webhook.assert_called()
            call_args = [call[0] for call in mock_webhook.call_args_list]
            assert any(args[0] == 1 and args[1] == "refunded" for args in call_args)
            print("  ✓ Chatbot webhook 'refunded' event successfully triggered for order 1")

        # =====================================================================
        # 4. Admin JWT Refund Flexibility (Can refund 'in_process' order)
        # =====================================================================
        print("\n--- 4. Testing Admin JWT Refund Flexibility ---")

        with patch("app.services.chatbot_notify.notify_chatbot_order_event", new_callable=AsyncMock) as mock_webhook:
            # Admin can refund Order 2 (which is 'in_process') without passing nomor_wa
            res = await client.post(
                "/orders/2/refund",
                json={"reason": "Admin membatalkan dan refund pesanan yang sedang diproses"},
                headers=admin_headers,
            )
            assert res.status_code == 200, f"Admin refund failed: {res.status_code} - {res.text}"
            assert res.json()["status"] == "cancelled"
            print("  ✓ Admin successfully refunded 'in_process' order without nomor_wa (200 OK)")

            # Check that refunded webhook was called for order 2
            mock_webhook.assert_called()
            call_args = [call[0] for call in mock_webhook.call_args_list]
            assert any(args[0] == 2 and args[1] == "refunded" for args in call_args)
            print("  ✓ Chatbot webhook 'refunded' event successfully triggered for order 2")

        # =====================================================================
        # 5. Payment Settlement 'paid' Webhook Trigger
        # =====================================================================
        print("\n--- 5. Testing Payment Settlement 'paid' Webhook Trigger ---")

        # Create new order & payment for settlement test
        async with TestSessionLocal() as db:
            order3 = Order(
                id=3,
                customer_id=1,
                status=OrderStatusEnum.pending,
                metode_pengiriman=MetodePengirimanEnum.pickup,
                total_harga_pesanan=Decimal("100000.00"),
                created_via="chatbot",
            )
            db.add(order3)
            await db.flush()

            inv3 = Invoice(
                id=3,
                order_id=3,
                nomor_invoice="INV-2026-003",
                total_tagihan=Decimal("100000.00"),
                status=InvoiceStatusEnum.unpaid,
            )
            db.add(inv3)
            await db.flush()

            pay3 = Payment(
                id=3,
                invoice_id=3,
                pg_transaction_id="pg-trans-003",
                jumlah_bayar=Decimal("100000.00"),
                payment_method="bank_transfer",
                payment_status=PaymentStatusEnum.pending,
                payment_type=PaymentTypeEnum.final,
            )
            db.add(pay3)
            await db.commit()

        # Simulate settlement webhook or _apply_transaction_status
        from app.services.payment_service import _apply_transaction_status
        with patch("app.services.chatbot_notify.notify_chatbot_order_event", new_callable=AsyncMock) as mock_webhook:
            async with TestSessionLocal() as db:
                p = await db.scalar(select(Payment).where(Payment.id == 3))
                await _apply_transaction_status(db, p, {"transaction_status": "settlement"})
                await db.commit()

            mock_webhook.assert_called_with(3, "paid")
            print("  ✓ Chatbot webhook 'paid' event successfully triggered on settlement!")

    app.dependency_overrides.clear()
    print("\n🎉 ALL CHATBOT REFUND & WEBHOOK INTEGRATION TESTS PASSED!\n")


@pytest.mark.asyncio
async def test_chatbot_refund_webhook():
    await run_chatbot_refund_tests()


if __name__ == "__main__":
    asyncio.run(run_chatbot_refund_tests())
