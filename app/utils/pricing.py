"""
Pricing utility function for auto-calculating product prices.

Formula:
    harga_jual = SUM(latest_purchase_cost × recipe.jumlah_dibutuhkan) × (1 + markup_percentage)

Falls back to StockItem.harga_per_satuan if no purchase history exists.
"""

from decimal import Decimal
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import desc
from app.models.product import Product
from app.models.recipe import Recipe
from app.models.stock_item import StockItem
from app.models.purchasing import PurchaseItem, Purchase


def calculate_product_price(
    db: Session,
    product_id: int,
    markup_percentage: Optional[Decimal] = None,
) -> Decimal:
    """
    Calculate product selling price using recipe costs and markup.

    Logic:
        a. Fetch all recipes for product
        b. For each recipe, find most recent PurchaseItem (ordered by purchase.created_at DESC)
        c. If no purchase, use stock_item.harga_per_satuan
        d. Sum all costs: total_cost = SUM(unit_cost × recipe.jumlah_dibutuhkan)
        e. Apply markup: use product.markup_percentage if not null, else default 0.30 (30%)
        f. Return: total_cost × (1 + markup_percentage)

    Args:
        db: Database session
        product_id: Product ID
        markup_percentage: Optional override markup. If None, uses product.markup_percentage or 0.30

    Returns:
        Decimal: Calculated selling price

    Raises:
        ValueError: If product has no recipes
    """
    # Get product and verify it exists
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise ValueError(f"Product {product_id} not found")

    # Get all recipes for this product
    recipes = db.query(Recipe).filter(Recipe.product_id == product_id).all()
    if not recipes:
        raise ValueError(f"Product {product_id} has no recipes")

    total_cost = Decimal("0")

    for recipe in recipes:
        # Try to get most recent purchase cost for this item
        latest_purchase_item = (
            db.query(PurchaseItem)
            .join(Purchase, PurchaseItem.purchase_id == Purchase.id)
            .filter(PurchaseItem.stock_item_id == recipe.stock_item_id)
            .order_by(desc(Purchase.created_at))
            .first()
        )

        if latest_purchase_item:
            unit_cost = Decimal(str(latest_purchase_item.harga_satuan))
        else:
            # Fallback to stock item's current price
            stock_item = db.query(StockItem).filter(
                StockItem.id == recipe.stock_item_id
            ).first()
            if not stock_item:
                raise ValueError(
                    f"Stock item {recipe.stock_item_id} not found for recipe {recipe.id}"
                )
            unit_cost = Decimal(str(stock_item.harga_per_satuan))

        # Add to total: unit_cost × jumlah_dibutuhkan
        cost_for_ingredient = unit_cost * Decimal(str(recipe.jumlah_dibutuhkan))
        total_cost += cost_for_ingredient

    # Determine markup percentage to use
    if markup_percentage is None:
        if product.markup_percentage is not None:
            markup_percentage = Decimal(str(product.markup_percentage))
        else:
            markup_percentage = Decimal("0.30")  # Default 30%
    else:
        markup_percentage = Decimal(str(markup_percentage))

    # Calculate final price: total_cost × (1 + markup_percentage)
    selling_price = total_cost * (Decimal("1") + markup_percentage)

    return selling_price.quantize(Decimal("0.01"))  # Round to 2 decimal places
