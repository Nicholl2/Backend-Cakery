import pytest
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, AsyncMock, MagicMock
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.main import app
from app.models.user import User
from app.models.role import Role
from app.models.customer import Customer
from app.models.order import Order, OrderItem, Invoice, OrderStatusEnum, InvoiceStatusEnum, MetodePengirimanEnum
from app.models.product import Product
from app.models.stock_item import StockItem, StockMovement, SatuanEnum, KategoriEnum
from app.models.recipe import Recipe
from app.models.payment import Payment, PaymentStatusEnum, PaymentTypeEnum
from app.core.config import settings
from app.services import order_service, payment_service, stock_service
from app.schemas.stock import StockAdjustmentCreate


@pytest.fixture
async def audit_test_session():
    TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
    engine = create_async_engine(TEST_DB_URL, poolclass=StaticPool, echo=False)
    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with SessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as db:
        role_owner = Role(id=1, nama_role="Owner", level=1)
        role_admin = Role(id=2, nama_role="Admin", level=2)
        role_staff = Role(id=3, nama_role="Staff", level=3)
        db.add_all([role_owner, role_admin, role_staff])

        user_owner = User(id=1, username="owner", email="owner@test.com", password_hash="hash", role_id=1, is_active=True, handles_takeover=True, nomor_wa_admin="08111111111")
        user_admin = User(id=2, username="admin", email="admin@test.com", password_hash="hash", role_id=2, is_active=True, handles_takeover=True, nomor_wa_admin="08222222222")
        db.add_all([user_owner, user_admin])

        # Master stock & product
        stock_flour = StockItem(
            id=1,
            nama_item="Tepung Terigu",
            satuan=SatuanEnum.gram,
            kategori=KategoriEnum.bahan_baku,
            harga_per_satuan=Decimal("20.00"),
            stok_tersedia=Decimal("1000.00"),
            alert_min_stok=Decimal("100.00"),
            version=0,
        )
        db.add(stock_flour)
        await db.flush()

        product = Product(
            id=1,
            nama_produk="Kue Tart",
            harga_jual=Decimal("150000.00"),
            hpp_total=Decimal("50000.00"),
            is_active=True,
            is_available=True,
            minimum_order=1,
        )
        db.add(product)
        await db.flush()

        recipe = Recipe(product_id=1, stock_item_id=1, jumlah_dibutuhkan=Decimal("100.00"))
        db.add(recipe)

        customer = Customer(id=1, nama="Budi Customer", nomor_wa="08123456789", alamat="Jl. Mawar No. 10")
        db.add(customer)
        await db.flush()

        # Order fixture
        fut_date = datetime.now(timezone.utc) + timedelta(days=5)
        order = Order(
            id=101,
            customer_id=1,
            status=OrderStatusEnum.pending,
            metode_pengiriman=MetodePengirimanEnum.pickup,
            total_harga_pesanan=Decimal("150000.00"),
            created_via="chatbot",
            fulfillment_date=fut_date,
        )
        db.add(order)
        await db.flush()

        item = OrderItem(
            id=1,
            order_id=101,
            product_id=1,
            jumlah=1,
            subtotal=Decimal("150000.00"),
            hpp_snapshot=Decimal("50000.00"),
        )
        invoice = Invoice(
            id=1,
            order_id=101,
            nomor_invoice="INV-TEST-101",
            total_tagihan=Decimal("150000.00"),
            status=InvoiceStatusEnum.unpaid,
        )
        db.add_all([item, invoice])
        await db.commit()

    yield SessionLocal

    app.dependency_overrides.clear()
    try:
        await engine.dispose()
    except RuntimeError:
        pass


@pytest.mark.asyncio
async def test_read_order_by_id_via_service_key(audit_test_session):
    """Test Kevin's feature: GET /orders/{order_id} via SERVICE_ORDERS_READ_KEY (header & query param)."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        with patch.object(settings, "service_orders_read_key", "valid-read-key"):
            # 1. Access via X-Service-Key header
            res_header = await client.get("/orders/101", headers={"X-Service-Key": "valid-read-key"})
            assert res_header.status_code == 200
            data = res_header.json()
            assert data["id"] == 101
            assert data["fulfillment_date"] is not None
            assert data["customer"]["nama"] == "Budi Customer"
            assert data["customer"]["nomor_wa"] == "08123456789"
            assert len(data["order_items"]) == 1
            assert data["order_items"][0]["product_id"] == 1
            assert data["payment_status"] == "unpaid"

            # 2. Access via query parameter ?service_key=...
            res_query = await client.get("/orders/101?service_key=valid-read-key")
            assert res_query.status_code == 200
            assert res_query.json()["id"] == 101

            # 3. Invalid key -> 401
            res_invalid = await client.get("/orders/101", headers={"X-Service-Key": "wrong-key"})
            assert res_invalid.status_code == 401

            # 4. Wrong capability scope -> 403
            with patch.object(settings, "service_customers_key", "cust-key"):
                res_wrong_scope = await client.get("/orders/101", headers={"X-Service-Key": "cust-key"})
                assert res_wrong_scope.status_code == 403


@pytest.mark.asyncio
async def test_chatbot_webhooks_for_processing_and_completed(audit_test_session):
    """Test outward chatbot webhook triggers when order transitions to processing (diproses) and completed (selesai)."""
    SessionLocal = audit_test_session
    async with SessionLocal() as db:
        with patch("app.services.chatbot_notify.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            with patch.object(settings, "chatbot_url", "http://chatbot.local"), \
                 patch.object(settings, "chatbot_internal_key", "secret-internal-key"):

                # 1. Transition pending -> in_process (should trigger 'processing')
                await order_service.update_order_status(db, 101, OrderStatusEnum.in_process.value)
                assert mock_client.post.called
                post_url = mock_client.post.call_args[0][0]
                assert "/webhook/internal/orders/101/processing" in post_url
                assert mock_client.post.call_args[1]["headers"]["X-Internal-Key"] == "secret-internal-key"

                mock_client.post.reset_mock()

                # 2. Transition in_process -> ready (should trigger 'ready')
                await order_service.update_order_status(db, 101, OrderStatusEnum.ready.value)
                assert mock_client.post.called
                assert "/webhook/internal/orders/101/ready" in mock_client.post.call_args[0][0]

                mock_client.post.reset_mock()

                # 3. Transition ready -> completed (should trigger 'completed')
                await order_service.update_order_status(db, 101, OrderStatusEnum.completed.value)
                assert mock_client.post.called
                assert "/webhook/internal/orders/101/completed" in mock_client.post.call_args[0][0]


@pytest.mark.asyncio
async def test_chatbot_webhook_for_created_order(audit_test_session):
    """Test outward chatbot webhook 'created' is sent after a new order is committed (WhatsApp notification to sellers)."""
    SessionLocal = audit_test_session
    async with SessionLocal() as db:
        db.add(Customer(id=2, nama="Sari Customer", nomor_wa="08198765432", alamat="Jl. Melati No. 5"))
        await db.commit()

        with patch("app.services.chatbot_notify.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            with patch.object(settings, "chatbot_url", "http://chatbot.local"), \
                 patch.object(settings, "chatbot_internal_key", "secret-internal-key"):

                order = await order_service.create_new_order(
                    db,
                    customer_id=2,
                    items=[{"product_id": 1, "jumlah": 1}],
                    metode_pengiriman="pickup",
                    fulfillment_date=datetime.now(timezone.utc) + timedelta(days=3),
                )

                assert mock_client.post.called
                post_url = mock_client.post.call_args[0][0]
                assert post_url.endswith(f"/webhook/internal/orders/{order.id}/created")
                assert mock_client.post.call_args[1]["headers"]["X-Internal-Key"] == "secret-internal-key"


@pytest.mark.asyncio
async def test_dp_order_status_transitions_without_enforcing_online_settlement(audit_test_session):
    """Ensure DP orders can transition to 'ready' and 'picked_up' without enforcing online settlement."""
    SessionLocal = audit_test_session
    async with SessionLocal() as db:
        # Create a DP payment
        order = await db.get(Order, 101)
        invoice = await db.scalar(select(Invoice).where(Invoice.order_id == 101))
        invoice.status = InvoiceStatusEnum.partial
        order.status = OrderStatusEnum.in_process
        payment = Payment(
            invoice_id=invoice.id,
            pg_transaction_id="dp-test-txn",
            pg_order_id="INV-TEST-101-PAY-1",
            jumlah_bayar=Decimal("75000.00"),
            payment_method="qris",
            payment_status=PaymentStatusEnum.success,
            payment_type=PaymentTypeEnum.dp,
            settled_at=datetime.now(timezone.utc),
        )
        db.add(payment)
        await db.commit()

        # Update order status to ready (allowed even with partial payment)
        ready_order = await order_service.update_order_status(db, 101, OrderStatusEnum.ready.value)
        assert ready_order.status == OrderStatusEnum.ready

        # Update order status to picked_up (allowed for cash-on-pickup)
        picked_order = await order_service.update_order_status(db, 101, OrderStatusEnum.picked_up.value)
        assert picked_order.status == OrderStatusEnum.picked_up


@pytest.mark.asyncio
async def test_stock_ledger_finite_and_negative_inventory_guards(audit_test_session):
    """Audit stock adjustments to guarantee no negative inventory and finite values."""
    SessionLocal = audit_test_session
    async with SessionLocal() as db:
        # 1. Attempt adjustment resulting in negative stock -> 409 Conflict
        with pytest.raises(Exception) as exc_neg:
            await stock_service.adjust_stock(
                db, 1, StockAdjustmentCreate(quantity_difference=Decimal("-2000.00"), reason="Opname error"), user_id=1
            )
        assert "negatif" in str(exc_neg.value).lower()

        # 2. Successful adjustment records finite StockMovement ledger entry
        adjusted = await stock_service.adjust_stock(
            db, 1, StockAdjustmentCreate(quantity_difference=Decimal("50.00"), unit_cost=Decimal("25.00"), reason="Restock bonus"), user_id=1
        )
        assert adjusted.stok_tersedia == Decimal("1050.00")
        movements = await stock_service.get_stock_movements(db, 1)
        assert any(m.reason == "Restock bonus" and m.quantity == Decimal("50.00") for m in movements)


@pytest.mark.asyncio
async def test_verify_existing_endpoint_standards(audit_test_session):
    """Verify endpoint naming standards: /reports/financial-summary and /admin/takeover-handlers."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Financial summary endpoint
        with patch.object(settings, "service_financial_summary_key", "fin-key"):
            res_fin = await client.get("/reports/financial-summary", headers={"X-Service-Key": "fin-key"})
            assert res_fin.status_code == 200

        # 2. Takeover handlers endpoint
        with patch.object(settings, "service_customers_key", "cust-key"):
            res_to = await client.get("/admin/takeover-handlers", headers={"X-Service-Key": "cust-key"})
            assert res_to.status_code == 200
            assert "numbers" in res_to.json()

        # 3. WhatsApp verification key support
        with patch.object(settings, "service_wa_verification_key", "wa-key"):
            res_wa = await client.post(
                "/auth/verify/wa/confirm",
                headers={"X-Service-Key": "wa-key"},
                json={"nonce": "dummy-nonce", "sender_phone": "08123456789"},
            )
            # Route accessed successfully with service key authentication (passed auth, nonce not found in db -> 404)
            assert res_wa.status_code == 404

        # 4. Reject when key is invalid
        res_wa_bad = await client.post(
            "/auth/verify/wa/confirm",
            headers={"X-Service-Key": "bad-key"},
            json={"nonce": "dummy-nonce", "sender_phone": "08123456789"},
        )
        assert res_wa_bad.status_code == 401
