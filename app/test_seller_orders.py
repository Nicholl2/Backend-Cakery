import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asyncio
from decimal import Decimal
import httpx
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy import select

from app.core.database import Base, get_db
from app.core.security import create_access_token
from app.main import app
from app.models.role import Role
from app.models.user import User
from app.models.buyer import Buyer
from app.models.product import Product
from app.models.stock_item import StockItem, SatuanEnum, KategoriEnum
from app.models.recipe import Recipe


def select_stock(item_id: int):
    return select(StockItem).where(StockItem.id == item_id)


async def run_seller_order_tests():
    print("🚀 Starting Seller Orders, Custom Orders & Stock Logic Tests...")

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
        owner_role = Role(id=1, nama_role="Owner", level=1)
        admin_role = Role(id=2, nama_role="Admin", level=2)
        staff_role = Role(id=3, nama_role="Staff", level=3)
        buyer_role = Role(id=4, nama_role="Buyer", level=4)
        db.add_all([owner_role, admin_role, staff_role, buyer_role])

        admin_user = User(
            id=1,
            username="admin@toti.com",
            email="admin@toti.com",
            password_hash="mockhash",
            role_id=2,
            is_active=True,
        )
        staff_user = User(
            id=2,
            username="staff@toti.com",
            email="staff@toti.com",
            password_hash="mockhash",
            role_id=3,
            is_active=True,
        )
        buyer_user = Buyer(
            id=1,
            name="Buyer Joni",
            email="joni@buyer.com",
            phone="081299998888",
            password_hash="mockhash",
            is_verified=True,
            is_active=True,
        )
        db.add_all([admin_user, staff_user, buyer_user])

        tepung = StockItem(
            id=1,
            nama_item="Tepung Terigu",
            satuan=SatuanEnum.gram,
            kategori=KategoriEnum.bahan_baku,
            harga_per_satuan=Decimal("0.02"),
            stok_tersedia=Decimal("1000.00"),
            alert_min_stok=Decimal("100.00"),
            version=0,
        )
        telur = StockItem(
            id=2,
            nama_item="Telur Ayam",
            satuan=SatuanEnum.pcs,
            kategori=KategoriEnum.bahan_baku,
            harga_per_satuan=Decimal("2000.00"),
            stok_tersedia=Decimal("50.00"),
            alert_min_stok=Decimal("10.00"),
            version=0,
        )
        db.add_all([tepung, telur])

        cake_product = Product(
            id=1,
            nama_produk="Kue Coklat Spesial",
            deskripsi="Kue coklat enak",
            kategori="Cake",
            harga_jual=Decimal("100000.00"),
            hpp_total=Decimal("40000.00"),
            is_active=True,
            minimum_order=1,
        )
        db.add(cake_product)
        await db.flush()

        recipe1 = Recipe(
            product_id=cake_product.id,
            stock_item_id=tepung.id,
            jumlah_dibutuhkan=Decimal("200.0000"),
            quantity_required=Decimal("200.0000"),
            unit="gram",
        )
        recipe2 = Recipe(
            product_id=cake_product.id,
            stock_item_id=telur.id,
            jumlah_dibutuhkan=Decimal("4.0000"),
            quantity_required=Decimal("4.0000"),
            unit="pcs",
        )
        db.add_all([recipe1, recipe2])
        await db.commit()

    token_admin = create_access_token(user_id=1, role_level=2, username="admin@toti.com")
    token_staff = create_access_token(user_id=2, role_level=3, username="staff@toti.com")
    token_buyer = create_access_token(user_id=1, role_level=4, username="joni@buyer.com", role="buyer")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # ── Test 1: RBAC on GET /orders ──
        print("\n1. Testing RBAC on GET /orders...")
        res_unauth = await client.get("/orders")
        assert res_unauth.status_code == 401, f"Expected 401, got {res_unauth.status_code}"
        print("✓ Unauthenticated request rejected (401)")

        res_staff = await client.get("/orders", headers={"Authorization": f"Bearer {token_staff}"})
        assert res_staff.status_code == 403, f"Expected 403, got {res_staff.status_code}"
        print("✓ Staff request rejected (403 Forbidden)")

        res_admin = await client.get("/orders", headers={"Authorization": f"Bearer {token_admin}"})
        assert res_admin.status_code == 200, f"Expected 200, got {res_admin.status_code}"
        assert res_admin.json() == []
        print("✓ Admin request granted (200 OK), empty list returned")

        # ── Test 2: Custom Order Creation (POST /orders/custom) ──
        print("\n2. Testing Custom Order Creation (POST /orders/custom)...")
        custom_payload = {
            "customer_name": "Ibu Ratna",
            "customer_phone": "081234567890",
            "customer_address": "Jl. Mawar No. 12, Jakarta",
            "metode_pengiriman": "delivery",
            "notes": "Tolong tuliskan 'Selamat Ulang Tahun Budi' dengan lilin angka 5",
            "payment_method_preference": "bank_transfer",
            "items": [
                {
                    "custom_product_name": "Custom Cake Doraemon 2 Tingkat",
                    "price": "350000.00",
                    "qty": 1,
                    "custom_decoration_charge": "50000.00"
                },
                {
                    "custom_product_name": "Cupcake Custom Mini",
                    "price": "25000.00",
                    "qty": 4,
                    "custom_decoration_charge": "0.00"
                }
            ]
        }
        res_custom = await client.post(
            "/orders/custom",
            json=custom_payload,
            headers={"Authorization": f"Bearer {token_admin}"}
        )
        assert res_custom.status_code == 201, f"Expected 201, got {res_custom.status_code}: {res_custom.text}"
        custom_data = res_custom.json()
        custom_order_id = custom_data["id"]
        print(f"✓ Custom order created successfully: ID={custom_order_id}")

        assert custom_data["total_harga_pesanan"] == "500000.00"
        assert custom_data["created_via"] == "seller"
        assert custom_data["status"] == "pending"
        assert custom_data["notes"] == custom_payload["notes"]
        assert custom_data["payment_method_preference"] == "bank_transfer"
        assert custom_data["customer"]["nama"] == "Ibu Ratna"
        assert custom_data["customer"]["nomor_wa"] == "6281234567890"
        assert custom_data["customer"]["alamat"] == "Jl. Mawar No. 12, Jakarta"
        assert len(custom_data["order_items"]) == 2
        assert custom_data["order_items"][0]["custom_product_name"] == "Custom Cake Doraemon 2 Tingkat"
        assert custom_data["order_items"][0]["product_id"] is None
        assert custom_data["invoice"] is not None
        assert custom_data["invoice"]["nomor_invoice"].startswith("INV-")
        assert custom_data["invoice"]["total_tagihan"] == "500000.00"
        assert custom_data["invoice"]["status"] == "unpaid"
        assert custom_data["amount_paid"] == "0.00"
        assert custom_data["amount_due"] == "500000.00"
        print("✓ Custom Order fields, customer relation, invoice and calculations verified 100%")

        # Verify stock was NOT deducted for custom order
        async with TestSessionLocal() as db:
            s_tepung = (await db.execute(select_stock(1))).scalar_one()
            s_telur = (await db.execute(select_stock(2))).scalar_one()
            assert s_tepung.stok_tersedia == Decimal("1000.00"), f"Expected 1000.00, got {s_tepung.stok_tersedia}"
            assert s_telur.stok_tersedia == Decimal("50.00"), f"Expected 50.00, got {s_telur.stok_tersedia}"
        print("✓ Stock bypass verified: Ingredients stock remained intact!")

        # ── Test 3: List Seller Orders (GET /orders) & Detail (GET /orders/{id}) ──
        print("\n3. Testing GET /orders and GET /orders/{order_id}...")
        res_list = await client.get("/orders", headers={"Authorization": f"Bearer {token_admin}"})
        assert res_list.status_code == 200
        orders_list = res_list.json()
        assert len(orders_list) == 1
        assert orders_list[0]["id"] == custom_order_id
        assert orders_list[0]["customer"]["nama"] == "Ibu Ratna"
        assert len(orders_list[0]["order_items"]) == 2
        print(f"✓ GET /orders returned {len(orders_list)} order with nested customer & items")

        res_detail = await client.get(f"/orders/{custom_order_id}", headers={"Authorization": f"Bearer {token_admin}"})
        assert res_detail.status_code == 200
        detail_data = res_detail.json()
        assert detail_data["id"] == custom_order_id
        assert detail_data["customer"]["nomor_wa"] == "6281234567890"
        print("✓ GET /orders/{order_id} detail verified")

        # ── Test 4: Buyer Order (POST /orders/buyer) with Stock Deduction ──
        print("\n4. Testing Buyer Order with Recipe Stock Deduction...")
        buyer_order_payload = {
            "metode_pengiriman": "delivery",
            "items": [
                {"product_id": 1, "jumlah": 2, "custom_decoration_charge": "10000.00"}
            ],
            "created_via": "web"
        }
        res_buyer_order = await client.post(
            "/orders/buyer",
            json=buyer_order_payload,
            headers={"Authorization": f"Bearer {token_buyer}"}
        )
        assert res_buyer_order.status_code == 201, f"Expected 201, got {res_buyer_order.status_code}: {res_buyer_order.text}"
        buyer_order_data = res_buyer_order.json()
        buyer_order_id = buyer_order_data["id"]
        print(f"✓ Buyer order created: ID={buyer_order_id}, Total={buyer_order_data['total_harga_pesanan']}")

        # Verify stock was deducted:
        # 2 cakes * 200g = 400g tepung (1000 - 400 = 600)
        # 2 cakes * 4 pcs = 8 pcs telur (50 - 8 = 42)
        async with TestSessionLocal() as db:
            s_tepung = (await db.execute(select_stock(1))).scalar_one()
            s_telur = (await db.execute(select_stock(2))).scalar_one()
            assert s_tepung.stok_tersedia == Decimal("600.00"), f"Expected 600.00, got {s_tepung.stok_tersedia}"
            assert s_telur.stok_tersedia == Decimal("42.00"), f"Expected 42.00, got {s_telur.stok_tersedia}"
            assert s_tepung.version == 1
            assert s_telur.version == 1
        print("✓ Stock deduction and optimistic locking version increment verified!")

        # ── Test 5: Update Order Status (PATCH /orders/{order_id}/status) ──
        print("\n5. Testing PATCH /orders/{order_id}/status to 'in_process' and 'cancelled'...")
        res_status1 = await client.patch(
            f"/orders/{buyer_order_id}/status",
            json={"status": "in_process"},
            headers={"Authorization": f"Bearer {token_admin}"}
        )
        assert res_status1.status_code == 200
        assert res_status1.json()["status"] == "in_process"
        print("✓ Order status updated to 'in_process'")

        # Update to cancelled -> Verify stock is restored!
        res_status_cancel = await client.patch(
            f"/orders/{buyer_order_id}/status",
            json={"status": "cancelled"},
            headers={"Authorization": f"Bearer {token_admin}"}
        )
        assert res_status_cancel.status_code == 200
        assert res_status_cancel.json()["status"] == "cancelled"
        print("✓ Order status updated to 'cancelled'")

        # Verify stock restored:
        # Tepung restored back to 1000.00 (version 2)
        # Telur restored back to 50.00 (version 2)
        async with TestSessionLocal() as db:
            s_tepung = (await db.execute(select_stock(1))).scalar_one()
            s_telur = (await db.execute(select_stock(2))).scalar_one()
            assert s_tepung.stok_tersedia == Decimal("1000.00"), f"Expected 1000.00, got {s_tepung.stok_tersedia}"
            assert s_telur.stok_tersedia == Decimal("50.00"), f"Expected 50.00, got {s_telur.stok_tersedia}"
            assert s_tepung.version == 2
            assert s_telur.version == 2
        print("✓ Stock restoration on order cancellation verified 100%!")

    app.dependency_overrides.clear()
    print("\n🎉 ALL SELLER ORDER & INVENTORY TESTS PASSED FLAWLESSLY!")


if __name__ == "__main__":
    asyncio.run(run_seller_order_tests())
