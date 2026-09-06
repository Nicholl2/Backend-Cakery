"""
Backward-compatibility alias for seeder functions located in app.core.database.
"""
from app.core.database import (
    ensure_role,
    ensure_user,
    ensure_buyer,
    ensure_supplier,
    ensure_stock_item,
    ensure_product,
    ensure_recipe,
    ensure_faq,
    seed_initial_data,
)

__all__ = [
    "ensure_role",
    "ensure_user",
    "ensure_buyer",
    "ensure_supplier",
    "ensure_stock_item",
    "ensure_product",
    "ensure_recipe",
    "ensure_faq",
    "seed_initial_data",
]
