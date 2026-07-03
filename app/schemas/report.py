from pydantic import BaseModel
from decimal import Decimal
from typing import List

class TopProductSummary(BaseModel):
    product_id: int
    nama_produk: str
    qty: int
    revenue: Decimal

    class Config:
        from_attributes = True

class FinancialReportSummary(BaseModel):
    revenue: Decimal
    expenses: Decimal
    order_count: int
    avg_order_value: Decimal
    top_products: List[TopProductSummary]

    class Config:
        from_attributes = True
