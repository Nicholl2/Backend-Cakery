import sys
sys.path.insert(0, '.')
import asyncio
from decimal import Decimal
from sqlalchemy import select
from app.core.database import AsyncSessionLocal, engine, Base
from app.core.migrations import ensure_product_columns, ensure_buyer_columns, ensure_stock_item_columns, ensure_recipe_columns
from app.services import purchasing_service, stock_service, product_service, recipe_service, review_service
from app.schemas.purchasing import SupplierCreate, SupplierUpdate
from app.schemas.stock import StockCreate, StockUpdate
from app.schemas.product import ProductCreate
from app.schemas.recipe import RecipeCreate
from app.schemas.review import ReviewCreate, ReviewUpdate
from app.repositories import buyer_repo, customer_repo, review_repo, product_repo, recipe_repo, stock_repo
from app.models.review import Review
from app.models.recipe import Recipe
from app.models.stock_item import StockItem
from app.models.purchasing import Supplier
from app.models.product import Product


async def run_tests():
    print("🚀 Starting Master Data integration tests...")

    # 1. Run migrations
    print("\nRunning database migrations...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await ensure_product_columns(conn)
        await ensure_buyer_columns(conn)
        await ensure_stock_item_columns(conn)
        await ensure_recipe_columns(conn)
    print("✓ Migrations completed successfully")

    # 2. Get DB Session
    async with AsyncSessionLocal() as db:
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
                
            buyer = await buyer_repo.create_buyer(
                db=db,
                name="Buyer Master",
                email=temp_email,
                phone=temp_phone,
                password_hash="mockpassword",
                is_verified=True
            )
            buyer_id = buyer.id
            buyer_phone = buyer.phone
            print(f"✓ Temporary buyer created: ID={buyer_id}, Phone={buyer_phone}")

            # Create review (calls buyer-to-customer mapping)
            review_data = ReviewCreate(
                product_id=prod_id,
                rating=5,
                komentar="Kue paling enak di dunia!"
            )
            review = await review_service.create_review(db, buyer_id, review_data)
            review_id = review.id
            print(f"✓ Review created: ID={review_id}, Rating={review.rating}, Customer Phone={review.customer.nomor_wa}")
            assert review_id is not None
            assert review.rating == 5
            assert review.customer.nomor_wa == buyer_phone

            # Check product aggregate rating
            # Refresh product
            product = await product_repo.get_by_id(db, prod_id)
            print(f"Product updated status: rating={product.rating}, review_count={product.review_count}")
            assert product.rating == 5.0
            assert product.review_count == 1

            # Update Review
            review_update = ReviewUpdate(rating=4, komentar="Agak manis, tapi ok")
            review = await review_service.update_review(db, review_id, buyer_id, review_update)
            assert review.rating == 4
            
            # Refresh product and check aggregate
            product = await product_repo.get_by_id(db, prod_id)
            print(f"Product after review update: rating={product.rating}, review_count={product.review_count}")
            assert product.rating == 4.0

            # Delete Review
            await review_service.delete_review(db, review_id, buyer_id)
            print("✓ Review deleted successfully")
            
            # Check aggregate
            product = await product_repo.get_by_id(db, prod_id)
            print(f"Product after review delete: rating={product.rating}, review_count={product.review_count}")
            assert product.rating == 0.0
            assert product.review_count == 0

            # --- 2.6 Clean Up ---
            print("\nCleaning up test data...")
            # Re-query all entities to avoid expired state or database session issues
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
            print("\n🎉 ALL TESTS PASSED SUCCESSFULLY! Master Data is fully verified.")

        except Exception as e:
            await db.rollback()
            print(f"\n❌ Test failed: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run_tests())
