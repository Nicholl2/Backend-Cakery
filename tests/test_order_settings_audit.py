import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asyncio
from decimal import Decimal
from unittest.mock import patch
import httpx
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import Base, get_db
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.role import Role
from app.models.user import User
from app.models.buyer import Buyer
from app.models.product import Product
from app.models.stock_item import StockItem, SatuanEnum, KategoriEnum
from app.models.recipe import Recipe
from app.models.order import Order, OrderItem, Invoice, OrderStatusEnum, InvoiceStatusEnum, MetodePengirimanEnum


async def run_audit_tests():
    print("🚀 Starting Frontend-Backend Audit Verification Tests...")

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

    async with TestSessionLocal() as db:
        # Seed Roles: 1=Owner, 2=Admin, 3=Staff, 4=Buyer
        owner_role = Role(id=1, nama_role="Owner", level=1)
        admin_role = Role(id=2, nama_role="Admin", level=2)
        staff_role = Role(id=3, nama_role="Staff", level=3)
        buyer_role = Role(id=4, nama_role="Buyer", level=4)
        db.add_all([owner_role, admin_role, staff_role, buyer_role])

        # Seed Users
        owner_user = User(
            id=1,
            username="owner_boss",
            email="owner@toti.com",
            phone_number="081211112222",
            nomor_wa_admin="081211112222",
            password_hash=hash_password("ownersecret123"),
            role_id=1,
            is_active=True,
            handles_takeover=True,
        )
        admin_user = User(
            id=2,
            username="admin_siti",
            email="admin@toti.com",
            phone_number="081233334444",
            nomor_wa_admin="081233334444",
            password_hash=hash_password("adminsecret123"),
            role_id=2,
            is_active=True,
            handles_takeover=False,
        )
        staff_user = User(
            id=3,
            username="staff_budi",
            email="staff@toti.com",
            phone_number="081255556666",
            nomor_wa_admin="081255556666",
            password_hash=hash_password("staffsecret123"),
            role_id=3,
            is_active=True,
            handles_takeover=False,
        )
        buyer_user = Buyer(
            id=1,
            name="Buyer Ani",
            email="ani@buyer.com",
            phone="081277778888",
            password_hash=hash_password("buyersecret123"),
            is_verified=True,
            is_active=True,
        )
        db.add_all([owner_user, admin_user, staff_user, buyer_user])

        # Seed Stock & Product
        tepung = StockItem(
            id=1,
            nama_item="Tepung Terigu",
            satuan=SatuanEnum.gram,
            kategori=KategoriEnum.bahan_baku,
            harga_per_satuan=Decimal("0.02"),
            stok_tersedia=Decimal("5000.00"),
            alert_min_stok=Decimal("100.00"),
            version=0,
        )
        db.add(tepung)

        cake = Product(
            id=1,
            nama_produk="Black Forest Cake Deluxe",
            deskripsi="Kue black forest lezat",
            kategori="Cake",
            harga_jual=Decimal("150000.00"),
            hpp_total=Decimal("60000.00"),
            is_active=True,
            minimum_order=1,
        )
        db.add(cake)
        await db.flush()

        recipe = Recipe(
            product_id=cake.id,
            stock_item_id=tepung.id,
            jumlah_dibutuhkan=Decimal("250.0000"),
            quantity_required=Decimal("250.0000"),
            unit="gram",
        )
        db.add(recipe)
        await db.commit()

    token_owner = create_access_token(user_id=1, role_level=1, username="owner_boss")
    token_admin = create_access_token(user_id=2, role_level=2, username="admin_siti")
    token_staff = create_access_token(user_id=3, role_level=3, username="staff_budi")
    token_buyer = create_access_token(user_id=1, role_level=4, username="ani@buyer.com", role="buyer")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # =====================================================================
        # ITEM 1: Penyesuaian RBAC Order Seller (Staff diizinkan)
        # =====================================================================
        print("\n--- 1. Testing RBAC Order Seller ---")
        
        # Staff calls GET /orders -> MUST be 200 OK (Not 403!)
        res = await client.get("/orders", headers={"Authorization": f"Bearer {token_staff}"})
        assert res.status_code == 200, f"Staff should be allowed on GET /orders, got {res.status_code}"
        print("  ✓ Staff allowed on GET /orders (200 OK)")

        # Staff creates custom order -> MUST be 201 Created
        custom_payload = {
            "customer_name": "Pak Hendra",
            "customer_phone": "081288889999",
            "customer_address": "Jl. Melati No. 5",
            "metode_pengiriman": "pickup",
            "notes": "Custom order dari staff",
            "items": [
                {
                    "custom_product_name": "Kue Custom Unicorn 3D",
                    "price": "250000.00",
                    "qty": 1,
                    "custom_decoration_charge": "25000.00"
                }
            ]
        }
        res = await client.post("/orders/custom", json=custom_payload, headers={"Authorization": f"Bearer {token_staff}"})
        assert res.status_code == 201, f"Staff should be allowed on POST /orders/custom, got {res.status_code} - {res.text}"
        order_data = res.json()
        custom_order_id = order_data["id"]
        print(f"  ✓ Staff created custom order (201 Created, ID: {custom_order_id})")

        # Staff gets detail order -> MUST be 200 OK
        res = await client.get(f"/orders/{custom_order_id}", headers={"Authorization": f"Bearer {token_staff}"})
        assert res.status_code == 200, f"Staff should be allowed on GET /orders/{custom_order_id}, got {res.status_code}"
        print("  ✓ Staff allowed on GET /orders/{id} (200 OK)")

        # Staff updates order status -> MUST be 200 OK
        res = await client.patch(
            f"/orders/{custom_order_id}/status",
            json={"status": "in_process"},
            headers={"Authorization": f"Bearer {token_staff}"}
        )
        assert res.status_code == 200, f"Staff should be allowed on PATCH /orders/{custom_order_id}/status, got {res.status_code}"
        assert res.json()["status"] == "in_process"
        print("  ✓ Staff allowed on PATCH /orders/{id}/status (200 OK)")

        # Buyer is still rejected (403) from seller order endpoints
        res_buyer = await client.get("/orders", headers={"Authorization": f"Bearer {token_buyer}"})
        assert res_buyer.status_code == 403
        print("  ✓ Buyer correctly rejected (403) from seller /orders")

        # =====================================================================
        # ITEM 2: Product Name di OrderItem Schema & Order Service
        # =====================================================================
        print("\n--- 2. Testing product_name on OrderItem ---")

        # Check custom order items product_name
        res = await client.get(f"/orders/{custom_order_id}", headers={"Authorization": f"Bearer {token_staff}"})
        custom_items = res.json()["order_items"]
        assert len(custom_items) > 0
        assert custom_items[0]["product_name"] == "Kue Custom Unicorn 3D", f"Expected 'Kue Custom Unicorn 3D', got {custom_items[0]['product_name']}"
        print(f"  ✓ Custom order item product_name populated: {custom_items[0]['product_name']}")

        # Buyer creates standard order from catalog
        buyer_order_payload = {
            "metode_pengiriman": "pickup",
            "items": [
                {
                    "product_id": 1,
                    "jumlah": 2,
                    "custom_decoration_charge": "10000.00"
                }
            ],
            "notes": "Pesanan catalog buyer"
        }
        res = await client.post("/orders/buyer", json=buyer_order_payload, headers={"Authorization": f"Bearer {token_buyer}"})
        assert res.status_code == 201, f"Buyer order failed: {res.text}"
        catalog_order = res.json()
        catalog_order_id = catalog_order["id"]

        # Check detail via Staff GET /orders/{id}
        res = await client.get(f"/orders/{catalog_order_id}", headers={"Authorization": f"Bearer {token_staff}"})
        assert res.status_code == 200
        cat_items = res.json()["order_items"]
        assert len(cat_items) > 0
        assert cat_items[0]["product_name"] == "Black Forest Cake Deluxe", f"Expected 'Black Forest Cake Deluxe', got {cat_items[0]['product_name']}"
        print(f"  ✓ Master product order item product_name populated: '{cat_items[0]['product_name']}' (no fallback to 'Product #ID')")

        # =====================================================================
        # ITEM 3: Fix Exception Polling Payment (401 Handling)
        # =====================================================================
        print("\n--- 3. Testing Payment Polling Exception Handling ---")

        # (a) Missing order -> 404 NOT 401
        res = await client.get("/payments/99999/status", headers={"Authorization": f"Bearer {token_staff}"})
        assert res.status_code == 404, f"Expected 404 for non-existent order, got {res.status_code}"
        print(f"  ✓ Polling missing order returns 404 Not Found (not 401): {res.status_code}")

        # (b) Existing order with valid token -> 200 OK
        res = await client.get(f"/payments/{catalog_order_id}/status", headers={"Authorization": f"Bearer {token_staff}"})
        assert res.status_code == 200, f"Expected 200 for valid order, got {res.status_code} - {res.text}"
        payment_status_data = res.json()
        assert payment_status_data["order_id"] == catalog_order_id
        assert payment_status_data["invoice_status"] == "unpaid"
        print(f"  ✓ Polling valid order returns 200 OK: invoice_status={payment_status_data['invoice_status']}")

        # (c) Simulate internal SQLAlchemyError in payment_service -> 500 NOT 401
        with patch("app.services.payment_service.get_payments_by_order", side_effect=SQLAlchemyError("Simulated DB Connection Down")):
            res = await client.get(f"/payments/{catalog_order_id}/status", headers={"Authorization": f"Bearer {token_staff}"})
            assert res.status_code == 500, f"Expected 500 on DB error, got {res.status_code}"
            assert res.status_code != 401, "CRITICAL: Must NOT return 401 on internal error"
            print(f"  ✓ Internal DB error during polling returns 500 Internal Server Error (not 401): {res.status_code}")

        # =====================================================================
        # ITEM 4: Modul Seller Settings & User Management Endpoints
        # =====================================================================
        print("\n--- 4. Testing Seller Settings & User Management Endpoints ---")

        # (a) GET /users/me for Staff
        res = await client.get("/users/me", headers={"Authorization": f"Bearer {token_staff}"})
        assert res.status_code == 200, f"GET /users/me failed: {res.text}"
        me_data = res.json()
        assert me_data["username"] == "staff_budi"
        assert me_data["role_id"] == 3
        assert me_data["role_name"] == "Staff"
        print(f"  ✓ GET /users/me returns logged-in staff: {me_data['username']} (role: {me_data['role_name']})")

        # (b) PUT /users/me to update profile
        update_payload = {
            "email": "budi_new@toti.com",
            "phone_number": "081299990000",
            "nomor_wa_admin": "081299990000"
        }
        res = await client.put("/users/me", json=update_payload, headers={"Authorization": f"Bearer {token_staff}"})
        assert res.status_code == 200, f"PUT /users/me failed: {res.text}"
        updated_me = res.json()
        assert updated_me["email"] == "budi_new@toti.com"
        assert updated_me["phone_number"] == "081299990000"
        print("  ✓ PUT /users/me updated profile successfully")

        # (c) POST /users/me/change-password
        # Incorrect old password -> 400
        res = await client.post(
            "/users/me/change-password",
            json={"old_password": "wrongpassword", "new_password": "newsecretstaff123"},
            headers={"Authorization": f"Bearer {token_staff}"}
        )
        assert res.status_code == 400, f"Expected 400 on wrong old password, got {res.status_code}"
        print("  ✓ POST /users/me/change-password rejected wrong old password (400 Bad Request)")

        # Correct old password -> 200 OK
        res = await client.post(
            "/users/me/change-password",
            json={"old_password": "staffsecret123", "new_password": "newsecretstaff123"},
            headers={"Authorization": f"Bearer {token_staff}"}
        )
        assert res.status_code == 200, f"Password change failed: {res.text}"
        print("  ✓ POST /users/me/change-password changed password successfully (200 OK)")

        # Verify new password by logging in
        login_res = await client.post("/auth/login", json={"username": "staff_budi", "password": "newsecretstaff123"})
        assert login_res.status_code == 200, f"Login with new password failed: {login_res.text}"
        print("  ✓ Logged in successfully with newly changed password")

        # (d) GET /users (Owner vs Staff/Admin RBAC)
        # Staff -> 403 Forbidden
        res = await client.get("/users", headers={"Authorization": f"Bearer {token_staff}"})
        assert res.status_code == 403, f"Staff should receive 403 on GET /users, got {res.status_code}"
        print("  ✓ Staff denied access to GET /users (403 Forbidden)")

        # Admin -> 403 Forbidden
        res = await client.get("/users", headers={"Authorization": f"Bearer {token_admin}"})
        assert res.status_code == 403, f"Admin should receive 403 on GET /users, got {res.status_code}"
        print("  ✓ Admin denied access to GET /users (403 Forbidden)")

        # Owner -> 200 OK with list of all internal users
        res = await client.get("/users", headers={"Authorization": f"Bearer {token_owner}"})
        assert res.status_code == 200, f"Owner failed on GET /users: {res.text}"
        users_list = res.json()
        assert len(users_list) >= 3
        usernames = [u["username"] for u in users_list]
        assert "owner_boss" in usernames
        assert "admin_siti" in usernames
        assert "staff_budi" in usernames
        print(f"  ✓ Owner granted access to GET /users: {len(users_list)} users returned")

        # (e) PUT /users/{id} (Edit other user by Owner)
        # Staff attempts to edit another user -> 403 Forbidden
        res = await client.put(
            "/users/2",
            json={"handles_takeover": True},
            headers={"Authorization": f"Bearer {token_staff}"}
        )
        assert res.status_code == 403
        print("  ✓ Staff denied editing other users (403 Forbidden)")

        # Owner edits admin user -> 200 OK
        res = await client.put(
            "/users/2",
            json={"handles_takeover": True, "email": "siti_updated@toti.com"},
            headers={"Authorization": f"Bearer {token_owner}"}
        )
        assert res.status_code == 200, f"Owner editing user failed: {res.text}"
        assert res.json()["handles_takeover"] is True
        assert res.json()["email"] == "siti_updated@toti.com"
        print("  ✓ Owner edited admin user successfully (200 OK)")

        # (f) Self-Deactivation Guard: Owner cannot deactivate or delete self
        res = await client.patch("/users/1/deactivate", headers={"Authorization": f"Bearer {token_owner}"})
        assert res.status_code == 400, f"Expected 400 when Owner deactivates self, got {res.status_code}"
        print("  ✓ Self-deactivation prevented for Owner (400 Bad Request)")

        res = await client.delete("/users/1", headers={"Authorization": f"Bearer {token_owner}"})
        assert res.status_code == 400, f"Expected 400 when Owner deletes self, got {res.status_code}"
        print("  ✓ Self-deletion prevented for Owner (400 Bad Request)")

        # (g) Deactivate user by Owner: PATCH /users/{id}/deactivate
        res = await client.patch("/users/3/deactivate", headers={"Authorization": f"Bearer {token_owner}"})
        assert res.status_code == 200, f"Deactivate user failed: {res.text}"
        assert res.json()["is_active"] is False
        print("  ✓ Owner deactivated staff user (is_active=False)")

        # (h) DELETE /users/{id} by Owner
        res = await client.delete("/users/3", headers={"Authorization": f"Bearer {token_owner}"})
        assert res.status_code == 200, f"Delete user failed: {res.text}"
        print(f"  ✓ Owner deleted/deactivated user 3: {res.json()}")

    app.dependency_overrides.clear()
    print("\n🎉 ALL AUDIT VERIFICATION TESTS PASSED SUCCESSFULLY!\n")


@pytest.mark.asyncio
async def test_order_settings_audit():
    await run_audit_tests()


if __name__ == "__main__":
    asyncio.run(run_audit_tests())
