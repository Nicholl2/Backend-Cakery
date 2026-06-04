from decimal import Decimal
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from app.repositories import recipe_repo, product_repo, stock_repo
from app.schemas.recipe import RecipeCreate, RecipeUpdate, RecipeOut, RecipeSummary


def _recipe_to_out(recipe, stock_item) -> RecipeOut:
    biaya = Decimal(str(recipe.jumlah_dibutuhkan)) * Decimal(str(stock_item.harga_per_satuan))
    return RecipeOut(
        id=recipe.id,
        product_id=recipe.product_id,
        stock_item_id=recipe.stock_item_id,
        jumlah_dibutuhkan=recipe.jumlah_dibutuhkan,
        nama_bahan=stock_item.nama_item,
        satuan=stock_item.satuan,
        harga_per_satuan=stock_item.harga_per_satuan,
        biaya_bahan=biaya,
        created_at=recipe.created_at,
    )


def get_recipe_summary(db: Session, product_id: int) -> RecipeSummary:
    """
    Tampilkan seluruh bahan + HPP total untuk satu produk — Use Case 3 (View recipe details).
    """
    product = product_repo.get_by_id(db, product_id)
    if not product:
        raise HTTPException(404, "Produk tidak ditemukan.")

    hpp, breakdown = recipe_repo.calculate_hpp(db, product_id)
    recipes = recipe_repo.get_by_product(db, product_id)

    # Bangun RecipeOut per baris
    bahan_list = []
    for r in recipes:
        bahan_list.append(_recipe_to_out(r, r.stock_item))

    return RecipeSummary(
        product_id=product_id,
        nama_produk=product.nama_produk,
        hpp_total=hpp,
        bahan=bahan_list,
    )


def add_ingredient(db: Session, product_id: int, data: RecipeCreate) -> RecipeSummary:
    """
    Tambah bahan ke resep produk — Use Case 3 (Add Ingredients).
    Otomatis recalculate HPP setelah penambahan.
    """
    product = product_repo.get_by_id(db, product_id)
    if not product:
        raise HTTPException(404, "Produk tidak ditemukan.")

    stock = stock_repo.get_by_id(db, data.stock_item_id)
    if not stock:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Ingredient not found — bahan baku tidak terdaftar di inventory.",
        )

    # Cek duplikat bahan dalam resep yang sama
    existing = recipe_repo.get_by_product_and_stock(db, product_id, data.stock_item_id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Item '{stock.nama_item}' sudah ada dalam resep produk ini. Gunakan endpoint update.",
        )

    recipe_repo.create(db, product_id, data.stock_item_id, data.jumlah_dibutuhkan)

    # ── Recalculate & simpan HPP ke tabel products ──
    recipe_repo.sync_hpp_to_product(db, product_id)
    db.commit()

    return get_recipe_summary(db, product_id)


def update_ingredient(
    db: Session, product_id: int, recipe_id: int, data: RecipeUpdate
) -> RecipeSummary:
    """
    Edit jumlah bahan — Use Case 3 (Edit Existing).
    HPP otomatis di-recalculate.
    """
    product = product_repo.get_by_id(db, product_id)
    if not product:
        raise HTTPException(404, "Produk tidak ditemukan.")

    recipe = recipe_repo.get_by_id(db, recipe_id)
    if not recipe or recipe.product_id != product_id:
        raise HTTPException(404, "Resep/bahan tidak ditemukan pada produk ini.")

    recipe_repo.update_qty(db, recipe, data.jumlah_dibutuhkan)
    recipe_repo.sync_hpp_to_product(db, product_id)
    db.commit()

    return get_recipe_summary(db, product_id)


def remove_ingredient(db: Session, product_id: int, recipe_id: int) -> RecipeSummary:
    """
    Hapus bahan dari resep — Use Case 3 (Delete).
    HPP otomatis di-recalculate.
    """
    product = product_repo.get_by_id(db, product_id)
    if not product:
        raise HTTPException(404, "Produk tidak ditemukan.")

    recipe = recipe_repo.get_by_id(db, recipe_id)
    if not recipe or recipe.product_id != product_id:
        raise HTTPException(404, "Resep/bahan tidak ditemukan pada produk ini.")

    recipe_repo.delete(db, recipe)
    recipe_repo.sync_hpp_to_product(db, product_id)
    db.commit()

    return get_recipe_summary(db, product_id)