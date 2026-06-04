from app.models import *
from fastapi import FastAPI
from app.core.database import Base, engine
from app.api.routes.stock import router as stock_router
from app.api.routes.pricing import router as pricing_router
from app.api.routes.product import router as product_router
from app.api.routes.recipe import router as recipe_router
from app.api.routes.purchasing import router as purchasing_router

app = FastAPI(title="Toti Cakery API")

app.include_router(pricing_router)
app.include_router(stock_router)
app.include_router(product_router)
app.include_router(recipe_router)
app.include_router(purchasing_router)

Base.metadata.create_all(bind=engine)

@app.get("/")
def root():
    return {"status": "ok", "app": "Toti Cakery Backend"}
