import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asyncio
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool
from app.core.database import Base
from app.core.migrations import ensure_product_columns, ensure_buyer_columns, ensure_stock_item_columns, ensure_recipe_columns, ensure_otp_columns
from app.services import purchasing_service, stock_service, product_service, recipe_service, review_service
from app.schemas.purchasing import SupplierCreate, SupplierUpdate
from app.schemas.stock import StockCreate, StockUpdate
from app.schemas.product import ProductCreate
from app.schemas.recipe import RecipeCreate
from app.schemas.review import ReviewCreate, ReviewUpdate
from app.repositories import buyer_repo, customer_repo, review_repo, product_repo, recipe_repo, stock_repo
from app.core.security import get_password_hash, verify_password
from app.models.review import Review
from app.models.recipe import Recipe
from app.models.stock_item import StockItem
from app.models.purchasing import Supplier
from app.models.product import Product
from app.models.user import User
from app.models.buyer import Buyer
from app.models.role import Role
from app.core.database import ensure_role, ensure_buyer, ensure_user

# Isolated in-memory SQLite engine for standalone & isolated test execution
test_engine = create_async_engine(
    "sqlite+aiosqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
    echo=False,
)
TestSessionLocal = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)


async def run_tests():
    print("🚀 Starting Master Data integration tests...")

    # 1. Run migrations
    print("\nRunning database migrations...")
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await ensure_product_columns(conn)
        await ensure_buyer_columns(conn)
        await ensure_stock_item_columns(conn)
        await ensure_recipe_columns(conn)
        await ensure_otp_columns(conn)
    print("✓ Migrations completed successfully")

    # 2. Get DB Session
    async with TestSessionLocal() as db:
        try:
            # Clean up existing test data to ensure idempotency
            print("\nCleaning up any existing test data from previous runs...")
            # 1. Clean up product, its recipes, and reviews
            existing_prod_res = await db.execute(select(Product).where(Product.nama_produk == "Kue Master Enak"))
            existing_prod = existing_prod_res.scalars().first()
            if existing_prod:
                # Delete reviews first
                reviews_res = await db.execute(select(Review).where(Review.product_id == existing_prod.id))
                reviews = reviews_res.scalars().all()
                for r in reviews:
                    await db.delete(r)
                # Delete recipes
                recipes_res = await db.execute(select(Recipe).where(Recipe.product_id == existing_prod.id))
                recipes = recipes_res.scalars().all()
                for r in recipes:
                    await db.delete(r)
                await db.delete(existing_prod)
                await db.commit()

            # 2. Clean up supplier and its stock items
            existing_sup_res = await db.execute(select(Supplier).where(Supplier.nama_supplier == "Test Supplier Master"))
            existing_sup = existing_sup_res.scalars().first()
            if existing_sup:
                stock_items_res = await db.execute(select(StockItem).where(StockItem.supplier_id == existing_sup.id))
                stock_items = stock_items_res.scalars().all()
                for item in stock_items:
                    recipes_res = await db.execute(select(Recipe).where(Recipe.stock_item_id == item.id))
                    recipes = recipes_res.scalars().all()
                    for r in recipes:
                        await db.delete(r)
                    await db.delete(item)
                await db.delete(existing_sup)
                await db.commit()
            print("✓ Leftover test data cleaned up successfully")

            # --- 2.1 Test Supplier CRUD ---
            print("\nTesting Supplier CRUD...")
            supplier_data = SupplierCreate(
                nama_supplier="Test Supplier Master",
                kontak_person="Contact Master",
                email="master@supplier.com",
                nomor_telepon="08123456789",
                alamat="Jalan Master 123",
                kota="Jakarta"
            )
            # Create
            supplier = await purchasing_service.create_supplier(db, supplier_data)
            supplier_id = supplier.id
            print(f"✓ Supplier created: ID={supplier_id}, Name={supplier.nama_supplier}")
            assert supplier_id is not None
            assert supplier.nama_supplier == "Test Supplier Master"

            # Read / List
            suppliers = await purchasing_service.get_all_suppliers(db, only_active=True)
            assert len(suppliers) > 0
            print("✓ List suppliers successful")

            # Update
            update_data = SupplierUpdate(kontak_person="Contact Updated")
            supplier = await purchasing_service.update_supplier(db, supplier.id, update_data)
            assert supplier.kontak_person == "Contact Updated"
            print("✓ Supplier updated successfully")

            # --- 2.2 Test StockItem CRUD ---
            print("\nTesting StockItem CRUD...")
            stock_data = StockCreate(
                nama_item="Bahan Kue Master",
                satuan="gram",
                kategori="bahan_baku",
                harga_per_satuan=Decimal("50.00"),
                stok_tersedia=Decimal("1000.00"),
                alert_min_stok=Decimal("100.00"),
                supplier_id=supplier.id
            )
            # Create
            stock_item = await stock_service.create_stock(db, stock_data)
            stock_item_id = stock_item.id
            print(f"✓ Stock item created: ID={stock_item_id}, Name={stock_item.nama_item}, Min Alert={stock_item.alert_min_stok}, Supplier ID={stock_item.supplier_id}")
            assert stock_item_id is not None
            assert stock_item.alert_min_stok == Decimal("100.00")
            assert stock_item.supplier_id == supplier_id
            assert stock_item.supplier is not None
            assert stock_item.supplier.nama_supplier == "Test Supplier Master"

            # Read
            retrieved_stock = await stock_service.get_stock_or_404(db, stock_item.id)
            assert retrieved_stock.nama_item == "Bahan Kue Master"
            print("✓ Retrieve stock item successful")

            # Update
            stock_update = StockUpdate(alert_min_stok=Decimal("200.00"))
            stock_item = await stock_service.update_stock(db, stock_item.id, stock_update)
            assert stock_item.alert_min_stok == Decimal("200.00")
            print("✓ Stock item updated successfully")

            # --- 2.3 Test Product CRUD & Nested Schemas ---
            print("\nTesting Product CRUD...")
            product_data = ProductCreate(
                nama_produk="Kue Master Enak",
                deskripsi="Kue rasa master",
                kategori="Kue",
                harga_jual=Decimal("150000.00"),
                is_active=True,
                minimum_order=1
            )
            product_out = await product_service.create_product(db, product_data)
            product = await product_service.get_product_or_404(db, product_out.id)
            print(f"✓ Product created: ID={product.id}, Name={product.nama_produk}")
            assert product.id is not None

            # --- 2.4 Test Recipe ---
            print("\nTesting Recipe CRUD...")
            recipe_data = RecipeCreate(
                stock_item_id=stock_item.id,
                jumlah_dibutuhkan=Decimal("10.5000"),
                quantity_required=Decimal("10.5000"),
                unit="gram"
            )
            # Create
            recipe_summary = await recipe_service.add_ingredient(db, product.id, recipe_data)
            print(f"✓ Recipe ingredient added. Recipes count={len(recipe_summary.bahan)}")
            assert len(recipe_summary.bahan) == 1
            recipe_out = recipe_summary.bahan[0]
            assert recipe_out.quantity_required == Decimal("10.5")
            assert recipe_out.unit == "gram"
            assert recipe_out.stock_item is not None
            assert recipe_out.stock_item.nama_item == "Bahan Kue Master"

            # Verify GET product details nested response
            print("Verifying nested product GET response...")
            prod_id = product.id
            db.expire_all()
            retrieved_product = await product_service.get_product_or_404(db, prod_id)
            print(f"DEBUG: retrieved_product recipes count: {len(retrieved_product.recipes) if retrieved_product.recipes is not None else 'None'}")
            # Serialize using Pydantic schema ProductOut to test nested recipes serialization
            from app.schemas.product import ProductOut
            p_serialized = ProductOut.model_validate(retrieved_product)
            print(f"DEBUG: p_serialized.recipes: {p_serialized.recipes}")
            assert p_serialized.recipes is not None
            assert len(p_serialized.recipes) == 1
            assert p_serialized.recipes[0].stock_item is not None
            assert p_serialized.recipes[0].stock_item.nama_item == "Bahan Kue Master"
            print("✓ Nested product serialization works perfectly!")

            # --- 2.5 Test Review CRUD ---
            print("\nTesting Review CRUD...")
            # Create a mock Buyer
            print("Creating temporary buyer...")
            temp_email = "buyer.master@test.com"
            temp_phone = "081999999999"
            
            # Clean up existing buyer if any
            existing_buyer = await buyer_repo.get_buyer_by_email(db, temp_email)
            if existing_buyer:
                await db.delete(existing_buyer)
                await db.commit()
                
            buyer = Buyer(
                name="Test Buyer",
                email=temp_email,
                phone=temp_phone,
                password_hash=get_password_hash("password123"),
                is_active=True,
                is_verified=True
            )
            db.add(buyer)
            await db.commit()
            await db.refresh(buyer)
            buyer_id = buyer.id
            print(f"✓ Temporary buyer created: ID={buyer_id}")

            # Create Review
            review_data = ReviewCreate(
                product_id=product.id,
                rating=5,
                komentar="Kue enak banget!",
                is_published=True
            )
            review_out = await review_service.create_review(db, buyer_id, review_data)
            print(f"✓ Review created: ID={review_out.id}, Rating={review_out.rating}, Komentar={review_out.komentar}")
            assert review_out.id is not None
            assert review_out.customer_id is not None
            assert review_out.rating == 5
            
            # Read Review
            review = await review_repo.get_by_id(db, review_out.id)
            assert review is not None
            assert review.product is not None
            assert review.product.nama_produk == "Kue Master Enak"
            assert review.customer is not None
            assert review.customer.nama == "Test Buyer"
            print("✓ Retrieve review successful with nested relationships")

            # Update Review
            review_update = ReviewUpdate(rating=4, komentar="Enak tapi agak manis")
            updated_review = await review_service.update_review(db, review.id, buyer_id, review_update)
            assert updated_review.rating == 4
            assert updated_review.komentar == "Enak tapi agak manis"
            print("✓ Review updated successfully")

            # Delete Review
            del_result = await review_service.delete_review(db, review.id, buyer_id)
            assert del_result is True
            deleted_check = await review_repo.get_by_id(db, review.id)
            assert deleted_check is None
            print("✓ Review deleted successfully")

            # --- 2.6 Test Buyer Seeder & Password Verification ---
            print("\nTesting Buyer Seeder & Password Verification...")
            # 1. Ensure Roles
            owner_role = await ensure_role(db, "Owner", 1)
            admin_role = await ensure_role(db, "Admin", 2)
            staff_role = await ensure_role(db, "Staff", 3)
            buyer_role = await ensure_role(db, "Buyer", 4)
            await db.commit()
            print(f"✓ Roles verified: Owner({owner_role.id}), Admin({admin_role.id}), Staff({staff_role.id}), Buyer({buyer_role.id})")

            # 2. Ensure Buyer with BUYER role_id
            seeded_buyer = await ensure_buyer(
                db,
                name="Aceng",
                email="aceng@gmail.com",
                phone="08123456789",
                password_plain="Aceng_123",
                role_id=buyer_role.id
            )
            await db.commit()

            # Verify buyer record
            assert seeded_buyer is not None
            assert seeded_buyer.name == "Aceng"
            assert seeded_buyer.email == "aceng@gmail.com"
            assert verify_password("Aceng_123", seeded_buyer.password_hash) is True
            print("✓ Buyer 'aceng@gmail.com' in 'buyers' table verified with get_password_hash('Aceng_123')")

            # Verify linked user record with BUYER role
            user_res = await db.execute(select(User).where((User.username == "aceng@gmail.com") | (User.email == "aceng@gmail.com")))
            user_aceng = user_res.scalars().first()
            assert user_aceng is not None
            assert user_aceng.role_id == buyer_role.id
            assert verify_password("Aceng_123", user_aceng.password_hash) is True
            print(f"✓ User 'aceng@gmail.com' in 'users' table linked with role_id={user_aceng.role_id} (Role: {buyer_role.nama_role})")

            # --- 2.7 Test Pembuatan User oleh Owner/Admin (Simulasi FE) ---
            print("\nTesting User Creation (Simulasi FE 'Tambah Pengguna')...")
            from app.schemas.user import UserCreate
            from app.services import user_service
            from fastapi import HTTPException
            
            fe_payload = {
                "nama_lengkap": "Nicholas Dinata",
                "username": "nicholas_test",
                "email": "nic@example.com",
                "nomor_wa": "081912345678",
                "role": "admin",
                "password": "Password123!"
            }
            # Verify alias works
            user_data = UserCreate.model_validate(fe_payload)
            print(f"Validated UserCreate: {user_data.model_dump(exclude={'password'})}")
            
            created_user = await user_service.create_user(db, user_data)
            print(f"✓ User created successfully: ID={created_user.id}, Username={created_user.username}")
            assert created_user.role_id == 2  # mapped from 'admin'
            assert created_user.email == "nic@example.com"
            assert created_user.nomor_wa_admin == "6281912345678"
            
            # Verify checking uniqueness
            try:
                await user_service.create_user(db, user_data)
                assert False, "Should raise exception for duplicate user"
            except HTTPException as e:
                print(f"✓ Duplicate user properly blocked: {e.detail}")
                assert e.status_code == 400

            # --- 2.8 Clean Up ---
            print("\nCleaning up test data...")
            # Re-query all entities to avoid expired state or database session issues
            if created_user.id:
                await db.delete(created_user)
            product = await product_repo.get_by_id(db, prod_id)
            if product:
                recipes = await recipe_repo.get_by_product(db, prod_id)
                for r in recipes:
                    await recipe_repo.delete(db, r)
                await product_repo.delete(db, product)
                print("Deleted recipes and product")

            stock_item = await stock_repo.get_by_id(db, stock_item_id)
            if stock_item:
                await stock_repo.delete(db, stock_item)
                print("Deleted stock item")

            supplier = await purchasing_service.get_supplier_or_404(db, supplier_id)
            if supplier:
                await purchasing_service.delete_supplier(db, supplier_id)
                print("Deleted supplier")

            buyer = await buyer_repo.get_buyer_by_id(db, buyer_id)
            if buyer:
                await db.delete(buyer)
            
            # Delete mapped customer
            customer = await customer_repo.get_by_nomor_wa(db, temp_phone)
            if customer:
                await db.delete(customer)
                
            await db.commit()
            print("Deleted buyer and customer")
            print("\n🎉 ALL TESTS PASSED SUCCESSFULLY! Master Data & Buyer Seeder are fully verified.")

        except Exception as e:
            await db.rollback()
            print(f"\n❌ Test failed: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)


async def test_master_data():
    """Pytest test runner for master data tests."""
    await run_tests()


if __name__ == "__main__":
    asyncio.run(run_tests())
