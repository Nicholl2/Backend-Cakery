import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import io
from decimal import Decimal
from unittest.mock import patch
import pytest
import httpx
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool
from sqlalchemy import select

from app.core.database import Base, get_db
from app.core.security import create_access_token
from app.main import app
from app.models.role import Role
from app.models.user import User
from app.models.product import Product, ProductImage
from app.schemas.product import ProductOut, ProductImageOut
from app.services import product_service
from app.repositories import product_repo


@pytest.fixture(name="test_session")
async def test_session_fixture():
    test_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    TestSessionLocal = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with TestSessionLocal() as session:
        yield session

    await test_engine.dispose()


@pytest.mark.asyncio
async def test_product_schema_multiple_images_and_backward_compatibility():
    """
    Test Pydantic schema ProductOut:
    1. images list serialization
    2. image_url backwards compatibility: fallback to primary image or first image.
    """
    # Case 1: Produk dengan 3 gambar, gambar ke-2 adalah primary
    images = [
        ProductImageOut(id=1, product_id=10, image_url="https://res.cloudinary.com/demo/image/upload/cake_angle1.jpg", is_primary=False),
        ProductImageOut(id=2, product_id=10, image_url="https://res.cloudinary.com/demo/image/upload/cake_front.jpg", is_primary=True),
        ProductImageOut(id=3, product_id=10, image_url="https://res.cloudinary.com/demo/image/upload/cake_slice.jpg", is_primary=False),
    ]
    p_out = ProductOut(
        id=10,
        nama_produk="Chocolate Fudge Cake",
        harga_jual=Decimal("120000.00"),
        hpp_total=Decimal("60000.00"),
        images=images,
    )
    assert len(p_out.images) == 3
    # Fallback image_url harus otomatis mengacu pada gambar primary (id=2)
    assert p_out.image_url == "https://res.cloudinary.com/demo/image/upload/cake_front.jpg"

    # Case 2: Produk dengan 2 gambar tanpa flag primary eksplisit -> fallback ke gambar pertama
    images_no_primary = [
        ProductImageOut(id=4, product_id=11, image_url="https://res.cloudinary.com/demo/image/upload/first.jpg", is_primary=False),
        ProductImageOut(id=5, product_id=11, image_url="https://res.cloudinary.com/demo/image/upload/second.jpg", is_primary=False),
    ]
    p_out2 = ProductOut(
        id=11,
        nama_produk="Vanilla Cake",
        harga_jual=Decimal("90000.00"),
        images=images_no_primary,
    )
    assert p_out2.image_url == "https://res.cloudinary.com/demo/image/upload/first.jpg"


@pytest.mark.asyncio
async def test_repository_eager_loading_and_image_management(test_session: AsyncSession):
    """
    Test Repository:
    1. Verifikasi eager loading selectinload(Product.images)
    2. add_product_images, set_primary_image, dan delete_product_image
    """
    db = test_session

    prod = Product(
        id=1,
        nama_produk="Strawberry Shortcake",
        harga_jual=Decimal("85000.00"),
        hpp_total=Decimal("40000.00"),
        is_active=True,
        is_available=True,
    )
    db.add(prod)
    await db.commit()

    # Tambahkan 3 gambar via repository
    image_urls = [
        "https://res.cloudinary.com/demo/img1.jpg",
        "https://res.cloudinary.com/demo/img2.jpg",
        "https://res.cloudinary.com/demo/img3.jpg",
    ]
    added = await product_repo.add_product_images(db, product_id=1, image_urls=image_urls, primary_index=0)
    assert len(added) == 3
    assert added[0].is_primary is True
    assert added[1].is_primary is False
    assert added[2].is_primary is False

    # Verifikasi eager loading via get_by_id
    fetched = await product_repo.get_by_id(db, 1)
    assert fetched is not None
    assert len(fetched.images) == 3
    assert fetched.primary_image_url == "https://res.cloudinary.com/demo/img1.jpg"

    # Verifikasi ubah primary image ke gambar kedua
    target_img_id = added[1].id
    await product_repo.set_primary_image(db, product_id=1, image_id=target_img_id)
    
    fetched_after_set_primary = await product_repo.get_by_id(db, 1)
    primary_item = next(img for img in fetched_after_set_primary.images if img.is_primary)
    assert primary_item.id == target_img_id
    assert primary_item.image_url == "https://res.cloudinary.com/demo/img2.jpg"
    assert fetched_after_set_primary.image_url == "https://res.cloudinary.com/demo/img2.jpg"

    # Verifikasi delete primary image -> reassign otomatis ke gambar tersisa
    await product_repo.delete_product_image(db, product_id=1, image_id=target_img_id)
    fetched_after_delete = await product_repo.get_by_id(db, 1)
    assert len(fetched_after_delete.images) == 2
    assert any(img.is_primary for img in fetched_after_delete.images)


@pytest.mark.asyncio
async def test_api_multiple_images_routes_and_backward_compatibility():
    """
    Test Integrasi API:
    - POST /products/{id}/images (multiple upload)
    - POST /products/{id}/image (single upload backward compatibility)
    - GET /products/{id} (response format verification)
    - PATCH /products/{id}/images/{image_id}/primary
    - DELETE /products/{id}/images/{image_id}
    """
    test_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    TestSessionLocal = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def override_get_db():
        async with TestSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    # Seed Admin User & Role
    async with TestSessionLocal() as db:
        role_admin = Role(id=1, nama_role="Admin", level=1)
        db.add(role_admin)
        admin_user = User(
            id=1,
            username="admin_test",
            password_hash="mock",
            role_id=1,
            is_active=True,
        )
        db.add(admin_user)
        
        prod = Product(
            id=100,
            nama_produk="Red Velvet Delight",
            harga_jual=Decimal("150000.00"),
            hpp_total=Decimal("70000.00"),
            is_active=True,
            is_available=True,
        )
        db.add(prod)
        await db.commit()

    token = create_access_token(user_id=1, role_level=1, username="admin_test", role="admin")
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Mock upload_image_to_cloudinary
        async def mock_upload(file, folder="toti-cakery/products", max_size=None):
            return f"https://res.cloudinary.com/toti/{file.filename}"

        with patch("app.services.product_service.upload_image_to_cloudinary", side_effect=mock_upload):
            # 1. POST /products/100/images (Upload 3 images)
            files = [
                ("files", ("angle1.png", io.BytesIO(b"fake_img_1"), "image/png")),
                ("files", ("angle2.png", io.BytesIO(b"fake_img_2"), "image/png")),
                ("files", ("angle3.png", io.BytesIO(b"fake_img_3"), "image/png")),
            ]
            data = {"primary_index": "1"} # Make angle2.png the primary
            res = await client.post("/products/100/images", files=files, data=data, headers=headers)
            assert res.status_code == 200, res.text
            res_json = res.json()
            assert len(res_json["images"]) == 3
            assert res_json["image_url"] == "https://res.cloudinary.com/toti/angle2.png"
            primary_img = next(img for img in res_json["images"] if img["is_primary"])
            assert primary_img["image_url"] == "https://res.cloudinary.com/toti/angle2.png"

            # 2. GET /products/100 -> Verify 3 images in response
            get_res = await client.get("/products/100")
            assert get_res.status_code == 200
            get_json = get_res.json()
            assert len(get_json["images"]) == 3
            assert get_json["image_url"] == "https://res.cloudinary.com/toti/angle2.png"

            # 3. PATCH /products/100/images/{img_id}/primary (Change primary to first image)
            first_img_id = get_json["images"][0]["id"]
            patch_res = await client.patch(f"/products/100/images/{first_img_id}/primary", headers=headers)
            assert patch_res.status_code == 200
            patch_json = patch_res.json()
            assert patch_json["image_url"] == get_json["images"][0]["image_url"]
            assert next(i for i in patch_json["images"] if i["id"] == first_img_id)["is_primary"] is True

            # 4. DELETE /products/100/images/{img_id}
            del_img_id = patch_json["images"][-1]["id"]
            del_res = await client.delete(f"/products/100/images/{del_img_id}", headers=headers)
            assert del_res.status_code == 200
            assert del_res.json()["deleted"] is True

            # Verify count after deletion
            final_get = await client.get("/products/100")
            assert len(final_get.json()["images"]) == 2

            # 5. Backward compatibility: POST /products/100/image (Single file upload)
            single_file = {"file": ("angle4.png", io.BytesIO(b"fake_img_4"), "image/png")}
            single_res = await client.post("/products/100/image", files=single_file, headers=headers)
            assert single_res.status_code == 200
            assert len(single_res.json()["images"]) == 3

    app.dependency_overrides.clear()
    await test_engine.dispose()
