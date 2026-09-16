from pydantic import BaseModel, ConfigDict, model_validator
from decimal import Decimal
from typing import List, Optional


class RecentOrderSummary(BaseModel):
    id: int
    customer_name: Optional[str] = None
    nama_customer: Optional[str] = None
    total_price: Optional[Decimal] = None
    total_harga: Optional[Decimal] = None
    total_harga_pesanan: Optional[Decimal] = None
    status: str

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="after")
    def sync_aliases(self):
        name = self.customer_name or self.nama_customer
        self.customer_name = name
        self.nama_customer = name

        price = self.total_price if self.total_price is not None else (self.total_harga if self.total_harga is not None else self.total_harga_pesanan)
        self.total_price = price
        self.total_harga = price
        self.total_harga_pesanan = price
        return self


class ReportSummary(BaseModel):
    total_products: int
    active_products: int
    total_revenue: Decimal
    total_orders: int
    recent_orders: List[RecentOrderSummary]

    model_config = ConfigDict(from_attributes=True)


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
