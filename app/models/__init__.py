from app.core.database import Base
from .product import Product
from .recipe import Recipe
from .stock_item import StockItem
from .price_history import PriceHistory
from .purchasing import Supplier, Purchase, PurchaseItem
from .role import Role
from .user import User  # Amankan satu sumber model User di sini
from .faq_item import FaqItem
from .expense import Expense

# Pastikan SEMUA model masuk ke sini agar otomatis ter-generate saat startup aplikasi
__all__ = [
    "Base",
    "Product",
    "Recipe",
    "StockItem",
    "PriceHistory",
    "Supplier",
    "Purchase",
    "PurchaseItem",
    "Role",
    "User",
    "FaqItem",
    "Expense"
]