import sys
import os
import asyncio
from unittest.mock import patch, MagicMock
from decimal import Decimal
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.order import Order, OrderStatusEnum, Invoice, InvoiceStatusEnum
from app.models.payment import Payment, PaymentStatusEnum, PaymentTypeEnum
from app.models.stock_item import StockItem
from app.services.order_service import cancel_and_refund_order
from app.services.payment_service import process_midtrans_webhook

@pytest.mark.asyncio
async def test_order_refund_flow():
    print("\n[TEST] Memulai test flow Refund Order DP/Paid...")
    
    TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
    test_engine = create_async_engine(TEST_DB_URL, poolclass=StaticPool, echo=False)
    TestSessionLocal = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    # Setup DB
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        
    async with TestSessionLocal() as db:
        # 1. Setup mock order, invoice, payment, stock
        from app.models.customer import Customer
        from app.models.product import Product
        from app.models.order import OrderItem, MetodePengirimanEnum
        
        customer = Customer(nomor_wa="62811223344", nama="Test User")
        db.add(customer)
        await db.flush()
        
        # Buat dummy stock untuk memastikan rollback bekerja
        from app.models.stock_item import SatuanEnum, KategoriEnum
        stock = StockItem(
            nama_item="Tepung", 
            stok_tersedia=Decimal("1000.00"), 
            version=1,
            harga_per_satuan=Decimal("10"),
            satuan=SatuanEnum.gram,
            kategori=KategoriEnum.bahan_baku
        )
        db.add(stock)
        await db.flush()
        
        order = Order(
            customer_id=customer.id,
            status=OrderStatusEnum.in_process, # Pesanan sudah diproses
            total_harga_pesanan=Decimal("100000.00"),
            metode_pengiriman=MetodePengirimanEnum.pickup
        )
        db.add(order)
        await db.flush()
        
        invoice = Invoice(
            order_id=order.id,
            nomor_invoice="INV-REFUND-001",
            total_tagihan=Decimal("100000.00"),
            status=InvoiceStatusEnum.partial # Sudah bayar DP
        )
        db.add(invoice)
        await db.flush()
        
        payment = Payment(
            invoice_id=invoice.id,
            pg_transaction_id="mock-pg-id-123",
            jumlah_bayar=Decimal("50000.00"),
            payment_method="qris",
            payment_status=PaymentStatusEnum.success,
            payment_type=PaymentTypeEnum.dp
        )
        db.add(payment)
        await db.commit()

        # 2. Panggil cancel_and_refund_order dengan mock HTTPX
        with patch('app.services.payment_service.httpx.AsyncClient') as mock_client:
            # Mock success response dari Midtrans Refund API
            from unittest.mock import AsyncMock
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "status_code": "200",
                "status_message": "Refund success",
                "refund_key": "refund-123"
            }
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)
            
            # Panggil service
            refund_res = await cancel_and_refund_order(db, order.id, "Stok habis")
            
            # Verifikasi mock terpanggil
            assert mock_client.return_value.__aenter__.return_value.post.called
            assert refund_res.refund_mode == "auto", f"Expected auto refund_mode, got {refund_res.refund_mode}"
            assert refund_res.status == "refunded"
            assert refund_res.payment_status == "refunded"

            # Verifikasi perubahan statenya
            await db.refresh(order)
            await db.refresh(invoice)
            await db.refresh(payment)
            
            assert order.status == OrderStatusEnum.refunded, f"Order status should be refunded, got {order.status}"
            assert invoice.status == InvoiceStatusEnum.refunded, f"Invoice status should be refunded, got {invoice.status}"
            assert payment.payment_status == PaymentStatusEnum.refunded, f"Payment status should be refunded, got {payment.payment_status}"
            print("✓ Service Refund memicu update DB dengan benar (Order refunded, Invoice refunded, Payment refunded, refund_mode=auto)")

        # 3. Test HTTP 412 Midtrans Fallback to Manual Refund (VA / QRIS)
        # Buat order baru dengan status in_process & payment success
        order412 = Order(
            customer_id=customer.id,
            status=OrderStatusEnum.in_process,
            total_harga_pesanan=Decimal("80000.00"),
            metode_pengiriman=MetodePengirimanEnum.pickup
        )
        db.add(order412)
        await db.flush()
        
        invoice412 = Invoice(
            order_id=order412.id,
            nomor_invoice="INV-REFUND-412",
            total_tagihan=Decimal("80000.00"),
            status=InvoiceStatusEnum.partial
        )
        db.add(invoice412)
        await db.flush()
        
        payment412 = Payment(
            invoice_id=invoice412.id,
            pg_transaction_id="mock-pg-id-412",
            jumlah_bayar=Decimal("40000.00"),
            payment_method="bank_transfer", # VA method unsupported for direct refund
            payment_status=PaymentStatusEnum.success,
            payment_type=PaymentTypeEnum.dp
        )
        db.add(payment412)
        await db.commit()

        with patch('app.services.payment_service.httpx.AsyncClient') as mock_client_412:
            mock_412 = MagicMock()
            mock_412.status_code = 412
            mock_412.json.return_value = {
                "status_code": "412",
                "status_message": "Merchant cannot refund this transaction"
            }
            mock_client_412.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_412)

            res_412 = await cancel_and_refund_order(db, order412.id, "Batal VA")
            assert res_412.refund_mode == "manual", f"Expected manual refund_mode for 412, got {res_412.refund_mode}"
            assert res_412.status == "refunded"
            assert res_412.payment_status == "refunded"
            print("✓ HTTP 412 Midtrans ditangani dengan baik dan jatuh ke refund_mode=manual (status refunded)")

    await test_engine.dispose()
    print("✅ Refund DP Flow Test Passed!")

@pytest.mark.asyncio
async def test_midtrans_webhook_refund():
    print("\n[TEST] Memulai test Webhook Refund...")
    
    TEST_DB_URL = "sqlite+aiosqlite:///:memory:"
    test_engine = create_async_engine(TEST_DB_URL, poolclass=StaticPool, echo=False)
    TestSessionLocal = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    # Setup DB
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        
    async with TestSessionLocal() as db:
        from app.models.customer import Customer
        from app.models.order import OrderItem, MetodePengirimanEnum
        
        customer = Customer(nomor_wa="6285555", nama="Test Webhook")
        db.add(customer)
        await db.flush()
        
        order = Order(
            customer_id=customer.id,
            status=OrderStatusEnum.in_process, 
            total_harga_pesanan=Decimal("100000.00"),
            metode_pengiriman=MetodePengirimanEnum.pickup
        )
        db.add(order)
        await db.flush()
        
        invoice = Invoice(
            order_id=order.id,
            nomor_invoice="INV-WH-001",
            total_tagihan=Decimal("100000.00"),
            status=InvoiceStatusEnum.paid 
        )
        db.add(invoice)
        await db.flush()
        
        payment = Payment(
            invoice_id=invoice.id,
            pg_transaction_id="mock-pg-wh-123",
            jumlah_bayar=Decimal("100000.00"),
            payment_method="qris",
            payment_status=PaymentStatusEnum.success,
            payment_type=PaymentTypeEnum.final
        )
        db.add(payment)
        await db.commit()
        
        # Test Webhook Payload for Refund
        import hashlib
        from app.core.config import settings
        
        midtrans_order_id = "INV-WH-001-PAY-999"
        gross_amount = "100000.00"
        status_code = "200"
        
        raw = f"{midtrans_order_id}{status_code}{gross_amount}{settings.midtrans_server_key}"
        sig = hashlib.sha512(raw.encode('utf-8')).hexdigest()
        
        payload = {
            "transaction_status": "refund",
            "transaction_id": "mock-pg-wh-123",
            "order_id": midtrans_order_id,
            "gross_amount": gross_amount,
            "status_code": status_code,
            "signature_key": sig
        }
        
        res = await process_midtrans_webhook(db, payload)
        assert res["status"] == "success"
        
        await db.refresh(payment)
        await db.refresh(invoice)
        await db.refresh(order)
        
        assert payment.payment_status == PaymentStatusEnum.refunded
        assert invoice.status == InvoiceStatusEnum.refunded
        assert order.status == OrderStatusEnum.cancelled
        print("✓ Webhook Refund diproses dengan benar dan mem-bypass Idempotency guard")

    await test_engine.dispose()
    print("✅ Webhook Refund Test Passed!")

if __name__ == "__main__":
    asyncio.run(test_order_refund_flow())
    asyncio.run(test_midtrans_webhook_refund())
