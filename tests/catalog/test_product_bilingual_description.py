import pytest
from pydantic import ValidationError

from app.models.product import Product
from app.schemas.product import ProductCreate, ProductOut, ProductUpdate


def test_product_schema_accepts_bilingual_fields_and_legacy_alias():
    created = ProductCreate(
        nama_produk="Choco Berry Cake",
        deskripsi_id="  Kue cokelat dengan stroberi.  ",
        deskripsi_en="Chocolate cake with strawberries.",
    )
    assert created.deskripsi == "Kue cokelat dengan stroberi."
    assert created.model_dump(exclude={"deskripsi_id"})["deskripsi_en"] == "Chocolate cake with strawberries."

    legacy = ProductCreate(nama_produk="Kue lama", deskripsi="Deskripsi lama")
    assert legacy.deskripsi == "Deskripsi lama"


def test_product_update_description_alias_and_null_clear():
    update = ProductUpdate(deskripsi_id="Deskripsi Indonesia", deskripsi_en=None)
    assert update.model_dump(exclude_unset=True)["deskripsi"] == "Deskripsi Indonesia"
    assert update.model_dump(exclude_unset=True)["deskripsi_en"] is None


def test_product_description_rejects_ambiguous_or_unsafe_input():
    with pytest.raises(ValidationError):
        ProductUpdate(deskripsi="Indonesia A", deskripsi_id="Indonesia B")
    with pytest.raises(ValidationError):
        ProductCreate(nama_produk="Produk", deskripsi_en="<script>alert(1)</script>")
    with pytest.raises(ValidationError):
        ProductCreate(nama_produk="Produk", deskripsi_id="x" * 5001)


def test_product_response_exposes_both_languages_and_legacy_value():
    product = Product(
        id=1,
        nama_produk="Choco Berry Cake",
        deskripsi="Kue cokelat dengan stroberi.",
        deskripsi_en="Chocolate cake with strawberries.",
        is_active=True,
        is_available=True,
        rating=0,
        review_count=0,
        sold_count=0,
        is_featured=False,
        minimum_order=1,
    )
    output = ProductOut.model_validate(product)
    assert output.deskripsi == output.deskripsi_id == "Kue cokelat dengan stroberi."
    assert output.deskripsi_en == "Chocolate cake with strawberries."
