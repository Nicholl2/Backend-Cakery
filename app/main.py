from contextlib import asynccontextmanager
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

try:
    from fastapi.staticfiles import StaticFiles
except ImportError:
    raise RuntimeError("fastapi.staticfiles is required to serve static files.")

# Safe creation of static directories (Aman untuk Vercel Read-Only System)
try:
    os.makedirs("static/products", exist_ok=True)
except OSError:
    pass  # Abaikan error di lingkungan serverless read-only

# Import database core
from app.core.database import Base, engine
from app import models  
from app.core.config import settings
from app.api.routes import auth, faq, expense
from app.api.routes.stock import router as stock_router
from app.api.routes.pricing import router as pricing_router
from app.api.routes.product import router as product_router
from app.api.routes.recipe import router as recipe_router
from app.api.routes.purchasing import router as purchasing_router
from app.api.routes.customer import router as customer_router, admin_router as customer_admin_router
from app.api.routes.order import router as order_router
from app.api.routes.payment import router as payment_router
from app.api.routes.report import router as report_router
from app.api.routes.user import router as user_router
from app.api.routes.review import router as review_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        from app.core.migrations import ensure_product_columns, ensure_buyer_columns, ensure_stock_item_columns, ensure_recipe_columns, ensure_otp_columns
        await ensure_product_columns(conn)
        await ensure_buyer_columns(conn)
        await ensure_stock_item_columns(conn)
        await ensure_recipe_columns(conn)
        await ensure_otp_columns(conn)
    yield
    pass

app = FastAPI(
    title="Toti Cakery API",
    description="Bakery management system with inventory, products, and financial tracking",
    version="1.0.0",
    lifespan=lifespan
)

# Hanya mount static jika direktori static benar-benar ada
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

origins = [origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
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
app.include_router(customer_admin_router)
app.include_router(order_router, prefix="/orders", tags=["Orders"])
app.include_router(payment_router, prefix="/payments", tags=["Payments"])
app.include_router(report_router, prefix="/reports", tags=["Reports"])
app.include_router(user_router, prefix="/users", tags=["Users"])
app.include_router(review_router, prefix="/reviews", tags=["Reviews"])