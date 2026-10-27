from contextlib import asynccontextmanager
import os
from fastapi import FastAPI, Request, Depends
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler

# ── Upload Size Limit Middleware ──────────────────────────────────────────────
MAX_BODY_SIZE = 5 * 1024 * 1024  # 5 MB

class MaxBodySizeMiddleware(BaseHTTPMiddleware):
    """Reject requests whose Content-Length exceeds MAX_BODY_SIZE (5 MB)."""

    async def dispatch(self, request: StarletteRequest, call_next):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > MAX_BODY_SIZE:
            return JSONResponse(
                status_code=413,
                content={"detail": "Payload melebihi batas maksimal 5 MB."},
            )
        return await call_next(request)

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
from app.api.routes.customer import (
    router as customer_router,
    admin_router as customer_admin_router,
    buyer_router,
)
from app.api.routes.order import router as order_router
from app.api.routes.payment import router as payment_router
from app.api.routes.report import router as report_router
from app.api.routes.user import router as user_router
from app.api.routes.review import router as review_router
from app.api.routes import admin_buyer, admin_whatsapp, public
from app.api.routes.settings import router as settings_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio
    import logging
    main_logger = logging.getLogger(__name__)

    from sqlalchemy import select, text
    from sqlalchemy.exc import ProgrammingError, OperationalError
    from app.core.database import AsyncSessionLocal, seed_initial_data
    from app.models.role import Role

    # 1. Verify database connectivity
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        main_logger.critical(f"Database connectivity check failed during startup: {exc}")
        raise RuntimeError(f"Database connectivity check failed: {exc}") from exc

    # 2. Verify schema initialization and run idempotent initial seed if needed
    try:
        async with AsyncSessionLocal() as db:
            try:
                role_check = await db.execute(select(Role).limit(1))
                has_roles = role_check.scalars().first() is not None
            except (ProgrammingError, OperationalError) as schema_err:
                main_logger.critical(
                    "Database schema is not initialized ('roles' table not found). "
                    "Run database bootstrap or migrations before starting the API."
                )
                raise RuntimeError(
                    "Database schema is not initialized. Run 'python -m app.db_bootstrap --confirm-empty' "
                    "or 'alembic upgrade head' before starting the application."
                ) from schema_err

            if not has_roles:
                main_logger.info("Roles table is empty. Running idempotent initial master data seed...")
                await seed_initial_data(db)
                main_logger.info("Initial seeding completed successfully.")
    except Exception as e:
        main_logger.critical(f"Critical initialization failure during startup: {e}")
        raise

    email_worker_task = None
    order_lifecycle_worker_task = None
    if settings.email_worker_enabled:
        from app.services.email_worker import run_email_worker

        email_worker_task = asyncio.create_task(
            run_email_worker(), name="email-outbox-worker"
        )

    if settings.order_lifecycle_worker_enabled:
        from app.services.order_lifecycle_service import run_order_lifecycle_worker

        order_lifecycle_worker_task = asyncio.create_task(
            run_order_lifecycle_worker(), name="order-lifecycle-worker"
        )

    try:
        yield
    finally:
        if email_worker_task is not None:
            email_worker_task.cancel()
            await asyncio.gather(email_worker_task, return_exceptions=True)
        if order_lifecycle_worker_task is not None:
            order_lifecycle_worker_task.cancel()
            await asyncio.gather(order_lifecycle_worker_task, return_exceptions=True)


app = FastAPI(
    title="Toti Cakery API",
    description="Bakery management system with inventory, products, and financial tracking",
    version="1.0.0",
    lifespan=lifespan,
    root_path="/api",
)

# ── RATE LIMITER (SlowAPI) ───────────────────────────────────────────────────
from app.core.rate_limiter import limiter
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Hanya mount static jika direktori static benar-benar ada
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

origins = [
    origin.strip().strip("'\"").rstrip("/")
    for origin in settings.cors_origins.split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── UPLOAD SIZE LIMIT ────────────────────────────────────────────────────────
app.add_middleware(MaxBodySizeMiddleware)

from app.api.routes.notification import router as notification_router

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
app.include_router(admin_buyer.router)
app.include_router(buyer_router, prefix="/buyers", tags=["Buyers"])
app.include_router(buyer_router, prefix="/v1/buyers", tags=["Buyers"])
app.include_router(buyer_router, prefix="/api/v1/buyers", tags=["Buyers"])
app.include_router(order_router, prefix="/orders", tags=["Orders"])
app.include_router(payment_router, prefix="/payments", tags=["Payments"])
app.include_router(report_router, prefix="/reports", tags=["Reports"])
app.include_router(user_router, prefix="/users", tags=["Users"])
app.include_router(settings_router)
app.include_router(review_router, prefix="/reviews", tags=["Reviews"])
app.include_router(notification_router)
app.include_router(admin_whatsapp.router)
app.include_router(public.router)

from app.schemas.order import OrderOut
from app.api.routes.order import list_buyer_orders

app.add_api_route(
    "/buyer",
    list_buyer_orders,
    methods=["GET"],
    response_model=list[OrderOut],
    tags=["Orders"],
    include_in_schema=False,
)


# ── HEALTH CHECK ─────────────────────────────────────────────────────────────
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db

@app.get("/health", tags=["Health"])
async def health_check(db: AsyncSession = Depends(get_db)):
    """Check API and database connectivity."""
    try:
        await db.execute(text("SELECT 1"))
        return {"status": "ok", "database": "connected"}
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"status": "error", "database": "disconnected"},
        )
