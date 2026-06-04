#!/usr/bin/env python3
"""Quick test to verify all imports work"""

import sys
sys.path.insert(0, '.')

try:
    print("Testing model imports...")
    from app.models.stock_item import StockItem
    from app.models.product import Product
    from app.models.recipe import Recipe
    from app.models.auth import User
    from app.models.price_history import PriceHistory
    from app.models.purchasing import Supplier, Purchase, PurchaseItem
    print("✓ All models imported successfully")
except Exception as e:
    print(f"✗ Model import error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

try:
    print("\nTesting schema imports...")
    from app.schemas.stock import StockOut
    from app.schemas.product import ProductOut
    from app.schemas.recipe import RecipeSummary
    from app.schemas.purchasing import SupplierOut, PurchaseOut
    print("✓ All schemas imported successfully")
except Exception as e:
    print(f"✗ Schema import error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

try:
    print("\nTesting service imports...")
    from app.services.stock_service import create_stock
    from app.services.product_service import create_product
    from app.services.recipe_service import get_recipe_summary
    from app.services.purchasing_service import create_supplier
    print("✓ All services imported successfully")
except Exception as e:
    print(f"✗ Service import error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

try:
    print("\nTesting app import...")
    from app.main import app
    print(f"✓ FastAPI app imported successfully")
    print(f"  - App title: {app.title}")
    print(f"  - Routes registered: {len(app.routes)}")
except Exception as e:
    print(f"✗ App import error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\n✅ All imports successful! Application is ready.")
