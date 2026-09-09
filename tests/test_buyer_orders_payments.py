import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asyncio
from decimal import Decimal
import httpx
from sqlalchemy import select

from app.core.database import AsyncSessionLocal, engine, Base
from app.core.config import settings
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
from app.models.buyer import Buyer
from app.models.product import Product
from app.models.customer import Customer
from app.models.order import Order
from app.repositories import buyer_repo, customer_repo
from app.core.security import create_access_token


async def run_tests():
    print("🚀 Starting Buyer Orders & Payments Integration Tests...")

    # 1. Ensure Migrations and Seed
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await ensure_product_columns(conn)
        await ensure_buyer_columns(conn)
        await ensure_stock_item_columns(conn)
        await ensure_recipe_columns(conn)
        await ensure_otp_columns(conn)
        await ensure_order_columns(conn)

    async with AsyncSessionLocal() as db:
        await seed_initial_data(db)

        # Retrieve a seeded product
        prod_res = await db.execute(select(Product).where(Product.is_active == True))
        product = prod_res.scalars().first()
        assert product is not None, "Product must exist after seeding"
        prod_id = product.id
        print(f"✓ Using Product: ID={prod_id}, Name={product.nama_produk}, Price={product.harga_jual}")

        # Setup 2 Test Buyers
        b1_email = "test.buyer1@test.com"
        b1_phone = "081911111111"
        b2_email = "test.buyer2@test.com"
        b2_phone = "081922222222"

        for em in [b1_email, b2_email]:
            b = await buyer_repo.get_buyer_by_email(db, em)
            if b:
                await db.delete(b)
        for ph in [b1_phone, b2_phone, "089999999999"]:
            c = await customer_repo.get_by_nomor_wa(db, ph)
            if c:
                ord_res = await db.execute(select(Order).where(Order.customer_id == c.id))
                for o in ord_res.scalars().all():
                    await db.delete(o)
                await db.delete(c)
        await db.commit()

        buyer1 = await buyer_repo.create_buyer(
            db, name="Buyer Satu", email=b1_email, phone=b1_phone, password_hash="hash1", is_verified=True
        )
        buyer2 = await buyer_repo.create_buyer(
            db, name="Buyer Dua", email=b2_email, phone=b2_phone, password_hash="hash2", is_verified=True
        )
        buyer1_id = buyer1.id
        buyer2_id = buyer2.id

    # Create JWT Tokens
    token_buyer1 = create_access_token(user_id=buyer1_id, role_level=0, username=b1_email, role="buyer")
    token_buyer2 = create_access_token(user_id=buyer2_id, role_level=0, username=b2_email, role="buyer")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Test 1: Create Order via Buyer JWT (POST /orders/buyer)
        print("\n1. Testing POST /orders/buyer with Buyer 1 JWT...")
        order_payload = {
            "metode_pengiriman": "delivery",
            "items": [
                {"product_id": prod_id, "jumlah": 1, "custom_decoration_charge": "0.00"}
            ],
            "created_via": "web"
        }
        res = await client.post(
            "/orders/buyer",
            json=order_payload,
            headers={"Authorization": f"Bearer {token_buyer1}"}
        )
        assert res.status_code == 201, f"Expected 201, got {res.status_code}: {res.text}"
        order1_data = res.json()
        order1_id = order1_data["id"]
        print(f"✓ Order created successfully: ID={order1_id}, Total={order1_data['total_harga_pesanan']}")
        assert order1_data["status"] == "pending"
        assert order1_data["created_via"] == "web"
        assert len(order1_data["items"]) == 1

        # Test 2: Unauthenticated POST /orders/buyer should fail (401)
        print("\n2. Testing unauthenticated POST /orders/buyer...")
        res_unauth = await client.post("/orders/buyer", json=order_payload)
        assert res_unauth.status_code == 401
        print("✓ Unauthenticated request rejected (401)")

        # Test 3: List Buyer Orders (GET /orders/buyer)
        print("\n3. Testing GET /orders/buyer for Buyer 1...")
        res_list = await client.get(
            "/orders/buyer",
            headers={"Authorization": f"Bearer {token_buyer1}"}
        )
        assert res_list.status_code == 200
        orders_list = res_list.json()
        assert len(orders_list) >= 1
        assert any(o["id"] == order1_id for o in orders_list)
        print(f"✓ List orders returned {len(orders_list)} orders")

        # Test 4: Buyer 2 List Orders should NOT contain Buyer 1's order
        print("\n4. Testing GET /orders/buyer for Buyer 2 (should be empty)...")
        res_list2 = await client.get(
            "/orders/buyer",
            headers={"Authorization": f"Bearer {token_buyer2}"}
        )
        assert res_list2.status_code == 200
        assert len(res_list2.json()) == 0
        print("✓ Isolation verified: Buyer 2 has 0 orders")

        # Test 5: Get Specific Order Detail (GET /orders/buyer/{id})
        print("\n5. Testing GET /orders/buyer/{order_id} by Owner (Buyer 1)...")
        res_detail = await client.get(
            f"/orders/buyer/{order1_id}",
            headers={"Authorization": f"Bearer {token_buyer1}"}
        )
        assert res_detail.status_code == 200
        detail_data = res_detail.json()
        assert detail_data["id"] == order1_id
        assert detail_data["invoice"] is not None
        print(f"✓ Detail order verified: Invoice={detail_data['invoice']['nomor_invoice']}")

        # Test 6: Cross-tenant access: Buyer 2 tries to GET Buyer 1's order (should 404)
        print("\n6. Testing cross-tenant access: Buyer 2 GET /orders/buyer/{order1_id}...")
        res_cross = await client.get(
            f"/orders/buyer/{order1_id}",
            headers={"Authorization": f"Bearer {token_buyer2}"}
        )
        assert res_cross.status_code == 404
        print("✓ Cross-tenant access blocked (404)")

        # Test 7: Chatbot POST /orders using X-Service-Key
        print("\n7. Testing chatbot POST /orders with X-Service-Key...")
        # Create or fetch customer for chatbot
        async with AsyncSessionLocal() as db:
            cb_cust, _ = await customer_repo.upsert(db, nomor_wa="089999999999", nama="Chatbot Customer")
            await db.commit()
            cb_cust_id = cb_cust.id

        cb_order_payload = {
            "customer_id": cb_cust_id,
            "metode_pengiriman": "pickup",
            "items": [
                {"product_id": prod_id, "jumlah": 1, "custom_decoration_charge": "0.00"}
            ],
            "created_via": "chatbot"
        }
        res_cb = await client.post(
            "/orders",
            json=cb_order_payload,
            headers={"X-Service-Key": settings.service_api_key}
        )
        assert res_cb.status_code == 201
        cb_order_data = res_cb.json()
        assert cb_order_data["created_via"] == "chatbot"
        print(f"✓ Chatbot order created: ID={cb_order_data['id']}")

        # Test 8: Payments endpoint with Buyer JWT (POST /payments)
        print("\n8. Testing POST /payments with Buyer 1 JWT...")
        # Invalid ownership payment: Buyer 2 tries to pay Buyer 1's order
        res_pay_unauth = await client.post(
            "/payments",
            json={
                "order_id": order1_id,
                "payment_method": "bank_transfer",
                "payment_type": "full",
                "amount": float(order1_data["total_harga_pesanan"])
            },
            headers={"Authorization": f"Bearer {token_buyer2}"}
        )
        assert res_pay_unauth.status_code == 404
        print("✓ Unauthorized payment attempt on another user's order blocked (404)")

        # Test GET /payments/{order_id}/status for Buyer 1
        print("\n9. Testing GET /payments/{order_id}/status with Buyer 1 JWT...")
        res_pay_status = await client.get(
            f"/payments/{order1_id}/status",
            headers={"Authorization": f"Bearer {token_buyer1}"}
        )
        assert res_pay_status.status_code == 200
        pay_stat = res_pay_status.json()
        assert pay_stat["order_id"] == order1_id
        assert pay_stat["invoice_status"] == "unpaid"
        print(f"✓ Payment status fetched: Invoice Status={pay_stat['invoice_status']}")

        # Chatbot checking status with X-Service-Key
        res_pay_stat_cb = await client.get(
            f"/payments/{order1_id}/status",
            headers={"X-Service-Key": settings.service_api_key}
        )
        assert res_pay_stat_cb.status_code == 200
        print("✓ Chatbot checked payment status with X-Service-Key (200)")

    # Cleanup test data
    async with AsyncSessionLocal() as db:
        for b_id in [buyer1_id, buyer2_id]:
            b = await buyer_repo.get_buyer_by_id(db, b_id)
            if b:
                await db.delete(b)
        for ph in [b1_phone, b2_phone, "089999999999"]:
            c = await customer_repo.get_by_nomor_wa(db, ph)
            if c:
                ord_res = await db.execute(select(Order).where(Order.customer_id == c.id))
                cust_orders = ord_res.scalars().all()
                for o in cust_orders:
                    await db.delete(o)
                await db.delete(c)
        await db.commit()

    print("\n🎉 ALL TESTS PASSED! Buyer Orders & Payments JWT flow is 100% verified.")


async def test_buyer_orders_payments():
    """Pytest test runner for buyer orders & payments tests."""
    await run_tests()


if __name__ == "__main__":
    asyncio.run(run_tests())
