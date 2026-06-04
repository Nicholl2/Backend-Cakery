from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.recipe import RecipeCreate, RecipeUpdate, RecipeSummary
from app.services import recipe_service

router = APIRouter(
    prefix="/products/{product_id}/recipes",
    tags=["Recipes (BOM)"],
)


@router.get("/", response_model=RecipeSummary,
            summary="Lihat seluruh bahan + HPP total produk — Use Case 3 (View recipe details)")
async def get_recipe(product_id: int, db: AsyncSession = Depends(get_db)):
    return await recipe_service.get_recipe_summary(db, product_id)


@router.post("/", response_model=RecipeSummary, status_code=201,
             summary="Tambah bahan ke resep — HPP otomatis diperbarui — Use Case 3 (Add Ingredient)")
async def add_ingredient(product_id: int, data: RecipeCreate, db: AsyncSession = Depends(get_db)):
    return await recipe_service.add_ingredient(db, product_id, data)


@router.put("/{recipe_id}", response_model=RecipeSummary,
            summary="Update jumlah bahan — HPP otomatis diperbarui — Use Case 3 (Edit Existing)")
async def update_ingredient(
    product_id: int, recipe_id: int, data: RecipeUpdate, db: AsyncSession = Depends(get_db)
):
    return await recipe_service.update_ingredient(db, product_id, recipe_id, data)


@router.delete("/{recipe_id}", response_model=RecipeSummary,
               summary="Hapus bahan dari resep — HPP otomatis diperbarui — Use Case 3 (Delete)")
async def remove_ingredient(product_id: int, recipe_id: int, db: AsyncSession = Depends(get_db)):
    return await recipe_service.remove_ingredient(db, product_id, recipe_id)
