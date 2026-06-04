from pydantic import BaseModel
from typing import List


class CostDetail(BaseModel):
    bahan: str
    qty: float
    unit_price: float
    cost: float


class PricingResponse(BaseModel):
    hpp: float
    margin_percent: float
    recommended_price: float
    breakdown: List[CostDetail]
