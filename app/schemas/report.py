from pydantic import BaseModel, ConfigDict
from decimal import Decimal
from typing import List


class TopProductSummary(BaseModel):
    product_id: int
    nama_produk: str
    qty: int
    revenue: Decimal

    model_config = ConfigDict(from_attributes=True)


class FinancialReportSummary(BaseModel):
    revenue: Decimal
    expenses: Decimal
    order_count: int
    avg_order_value: Decimal
    top_products: List[TopProductSummary]

    model_config = ConfigDict(from_attributes=True)


class FinancialReportDetail(BaseModel):
    total_revenue: Decimal
    total_expenses: Decimal
    total_hpp_cost: Decimal
    gross_profit: Decimal
    net_profit: Decimal

    model_config = ConfigDict(from_attributes=True)


class MostReviewedProduct(BaseModel):
    nama_produk: str
    avg_rating: float
    review_count: int

    model_config = ConfigDict(from_attributes=True)


class AnalyticsReport(BaseModel):
    total_customers: int
    conversion_rate_via_chatbot: float
    most_reviewed_product: MostReviewedProduct | None = None

    model_config = ConfigDict(from_attributes=True)
