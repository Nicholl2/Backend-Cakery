from contextlib import asynccontextmanager
from fastapi import FastAPI

# Import database core
from app.core.database import Base, engine
from app import models  
from app.api.routes import auth, faq, expense
from app.api.routes.stock import router as stock_router
from app.api.routes.pricing import router as pricing_router
from app.api.routes.product import router as product_router
from app.api.routes.recipe import router as recipe_router
from app.api.routes.purchasing import router as purchasing_router
from app.api.routes.customer import router as customer_router
from app.api.routes.order import router as order_router
from app.api.routes.payment import router as payment_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    pass

app = FastAPI(
    title="Toti Cakery API",
    description="Bakery management system with inventory, products, and financial tracking",
    version="1.0.0",
    lifespan=lifespan
)

# Include routers
app.include_router(auth.router, prefix="/auth", tags=["Authentication"])
app.include_router(faq.router, prefix="/faq", tags=["FAQ Management"])
app.include_router(expense.router, prefix="/expenses", tags=["Expenses"])
app.include_router(stock_router, prefix="/stock", tags=["Stock Items"])
app.include_router(pricing_router, prefix="/pricing", tags=["Pricing"])
app.include_router(product_router, prefix="/products", tags=["Products"])
app.include_router(recipe_router, prefix="/recipes", tags=["Recipes"])
app.include_router(purchasing_router, prefix="/purchases", tags=["Purchasing"])
app.include_router(customer_router, prefix="/customers", tags=["Customers"])
app.include_router(order_router, prefix="/orders", tags=["Orders"])
app.include_router(payment_router, prefix="/payments", tags=["Payments"])