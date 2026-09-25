from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession
from app.repositories.pricing_repo import get_recipe_with_cost


async def calculate_hpp(db: AsyncSession, product_id: int):
    rows = await get_recipe_with_cost(db, product_id)

    total = Decimal("0")
    detail = []

    for r in rows:
        cost = Decimal(str(r.jumlah_dibutuhkan)) * Decimal(str(r.harga_per_satuan))
        total += cost

        detail.append({
            "bahan": r.nama_bahan,
            "qty": float(r.jumlah_dibutuhkan),
            "unit_price": float(r.harga_per_satuan),
            "cost": float(cost)
        })

    return total, detail


def apply_margin(hpp: Decimal, margin_percent: float):
    return hpp * (1 + Decimal(str(margin_percent)) / 100)

