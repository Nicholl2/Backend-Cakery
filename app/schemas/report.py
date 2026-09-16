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
    cash_received: Decimal = Decimal("0.00")
    cash_refunded: Decimal = Decimal("0.00")
    net_cash_flow: Decimal = Decimal("0.00")
    order_count: int
    avg_order_value: Decimal
    top_products: List[TopProductSummary]

    model_config = ConfigDict(from_attributes=True)


class ProductProfitabilityItem(BaseModel):
    product_id: int
    nama_produk: str
    qty_sold: int
    total_revenue: Decimal
    total_hpp: Decimal
    gross_profit: Decimal
    margin_percentage: float

    model_config = ConfigDict(from_attributes=True)


class SupplierSpendingItem(BaseModel):
    supplier_id: int
    nama_supplier: str
    total_spending: Decimal
    purchase_count: int

    model_config = ConfigDict(from_attributes=True)


class FinancialReportDetail(BaseModel):
    revenue: Decimal
    total_revenue: Decimal
    cash_received: Decimal = Decimal("0.00")
    cash_refunded: Decimal = Decimal("0.00")
    net_cash_flow: Decimal = Decimal("0.00")
    hpp_total: Decimal
    total_hpp_cost: Decimal
    gross_profit: Decimal
    expenses_total: Decimal
    total_expenses: Decimal
    net_profit: Decimal
    outstanding_payments: Decimal
    non_refundable_dp_income: Decimal = Decimal("0.00")
    other_income: Decimal = Decimal("0.00")
    product_profitability: List[ProductProfitabilityItem] = []
    full_product_profitability: List[ProductProfitabilityItem] = []
    supplier_spending: List[SupplierSpendingItem] = []

    model_config = ConfigDict(from_attributes=True)


FinancialReportResponse = FinancialReportDetail


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
