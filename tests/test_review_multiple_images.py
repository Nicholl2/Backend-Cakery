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

from app.core.database import Base, get_db
from app.core.security import create_access_token
from app.main import app
from app.models.buyer import Buyer
from app.models.customer import Customer
from app.models.product import Product
from app.models.order import Order, OrderItem, OrderStatusEnum, MetodePengirimanEnum
from app.models.review import Review, ReviewImage
from app.schemas.review import ReviewOut, ReviewImageOut
from app.repositories import review_repo


@pytest.fixture(name="review_env")
async def review_env_fixture():
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

    async with TestSessionLocal() as db:
        # 1. Create Buyer 1 & Customer 1
        buyer1 = Buyer(
            id=10,
            name="Alice Baker",
            email="alice@example.com",
            phone="081234567890",
            password_hash="mock_hash",
            is_active=True,
        )
        # 2. Create Buyer 2 & Customer 2 (for BOLA testing)
        buyer2 = Buyer(
            id=20,
            name="Bob Stranger",
            email="bob@example.com",
            phone="089999999999",
            password_hash="mock_hash",
            is_active=True,
        )
        db.add_all([buyer1, buyer2])
        await db.flush()

        customer1 = Customer(
            id=10,
            nama="Alice Baker",
            nomor_wa="081234567890",
        )
        customer2 = Customer(
            id=20,
            nama="Bob Stranger",
            nomor_wa="089999999999",
        )
        db.add_all([customer1, customer2])
        await db.flush()

        # 3. Create Product
        prod = Product(
            id=100,
            nama_produk="Lapis Legit Special",
            kategori="Cake",
            harga_jual=Decimal("250000.00"),
            rating=0.0,
            review_count=0,
        )
        db.add(prod)
        await db.flush()

        # 4. Create Completed Order for Alice with Product
        order = Order(
            id=500,
            customer_id=customer1.id,
            status=OrderStatusEnum.completed,
            metode_pengiriman=MetodePengirimanEnum.delivery,
            total_harga_pesanan=Decimal("250000.00"),
        )
        db.add(order)
        await db.flush()

        order_item = OrderItem(
            id=1,
            order_id=order.id,
            product_id=prod.id,
            jumlah=1,
            subtotal=Decimal("250000.00"),
        )
        db.add(order_item)
        await db.commit()

    yield {
        "buyer1_id": 10,
        "buyer2_id": 20,
        "product_id": 100,
        "order_id": 500,
    }

    app.dependency_overrides.clear()
    await test_engine.dispose()


@pytest.mark.asyncio
async def test_review_schema_with_multiple_images():
    """Test serialization of ReviewOut with ReviewImageOut list."""
    images = [
        ReviewImageOut(id=1, review_id=5, image_url="https://res.cloudinary.com/demo/image1.jpg"),
        ReviewImageOut(id=2, review_id=5, image_url="https://res.cloudinary.com/demo/image2.jpg"),
    ]
    r_out = ReviewOut(
        id=5,
        order_id=500,
        product_id=100,
        customer_id=10,
        rating=5,
        comment="Enak banget kuenya!",
        created_at="2026-09-25T20:00:00Z",
        images=images,
    )
    assert len(r_out.images) == 2
    assert r_out.images[0].image_url == "https://res.cloudinary.com/demo/image1.jpg"
    assert r_out.images[1].image_url == "https://res.cloudinary.com/demo/image2.jpg"


@pytest.mark.asyncio
async def test_create_review_with_images_multipart(review_env):
    """
    Test POST /reviews with multipart/form-data uploading multiple images:
    - 2 photos uploaded
    - Verification that images are saved in database and eager-loaded
    """
    token1 = create_access_token(user_id=review_env["buyer1_id"], role_level=0, username="alice@example.com", role="buyer")
    headers1 = {"Authorization": f"Bearer {token1}"}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        async def mock_upload(file, folder="toti-cakery/reviews", max_size=None):
            return f"https://res.cloudinary.com/toti/{file.filename}"

        with patch("app.services.review_service.upload_image_to_cloudinary", side_effect=mock_upload):
            # 1. Submit review with 2 image files
            files = [
                ("images", ("foto_kue_1.png", io.BytesIO(b"fake_binary_1"), "image/png")),
                ("images", ("foto_kue_2.png", io.BytesIO(b"fake_binary_2"), "image/png")),
            ]
            form_data = {
                "order_id": str(review_env["order_id"]),
                "product_id": str(review_env["product_id"]),
                "rating": "5",
                "comment": "Lapis legit lembut dan wangi!",
            }
            res = await client.post("/reviews/", data=form_data, files=files, headers=headers1)
            assert res.status_code == 201, res.text
            res_json = res.json()

            assert res_json["rating"] == 5
            assert res_json["comment"] == "Lapis legit lembut dan wangi!"
            assert len(res_json["images"]) == 2
            assert res_json["images"][0]["image_url"] == "https://res.cloudinary.com/toti/foto_kue_1.png"
            assert res_json["images"][1]["image_url"] == "https://res.cloudinary.com/toti/foto_kue_2.png"

            review_id = res_json["id"]

            # 2. GET /reviews/product/{product_id}
            get_res = await client.get(f"/reviews/product/{review_env['product_id']}")
            assert get_res.status_code == 200
            get_json = get_res.json()
            assert len(get_json) == 1
            assert len(get_json[0]["images"]) == 2

            # 3. Add 1 more image: POST /reviews/{review_id}/images
            more_files = [
                ("images", ("foto_kue_3.png", io.BytesIO(b"fake_binary_3"), "image/png")),
            ]
            add_img_res = await client.post(f"/reviews/{review_id}/images", files=more_files, headers=headers1)
            assert add_img_res.status_code == 200
            assert len(add_img_res.json()["images"]) == 3

            # 4. Test BOLA: Buyer 2 tries to delete image from Buyer 1's review
            token2 = create_access_token(user_id=review_env["buyer2_id"], role_level=0, username="bob@example.com", role="buyer")
            headers2 = {"Authorization": f"Bearer {token2}"}

            image_to_delete_id = add_img_res.json()["images"][-1]["id"]
            del_unauth_res = await client.delete(f"/reviews/{review_id}/images/{image_to_delete_id}", headers=headers2)
            assert del_unauth_res.status_code == 403

            # 5. Buyer 1 deletes image: DELETE /reviews/{review_id}/images/{image_id}
            with patch("app.services.review_service.delete_image_from_cloudinary", return_value=True):
                del_auth_res = await client.delete(f"/reviews/{review_id}/images/{image_to_delete_id}", headers=headers1)
                assert del_auth_res.status_code == 200
                assert del_auth_res.json()["deleted"] is True

            # 6. Verify remaining images count
            detail_res = await client.get(f"/reviews/{review_id}")
            assert detail_res.status_code == 200
            assert len(detail_res.json()["images"]) == 2


@pytest.mark.asyncio
async def test_create_review_json_backward_compatibility(review_env):
    """Test POST /reviews with standard JSON body (ensures 0 regression for existing clients)."""
    token1 = create_access_token(user_id=review_env["buyer1_id"], role_level=0, username="alice@example.com", role="buyer")
    headers1 = {"Authorization": f"Bearer {token1}"}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        json_payload = {
            "order_id": review_env["order_id"],
            "product_id": review_env["product_id"],
            "rating": 4,
            "comment": "Sangat enak dan fresh!",
        }
        res = await client.post("/reviews/", json=json_payload, headers=headers1)
        assert res.status_code == 201
        res_json = res.json()
        assert res_json["rating"] == 4
        assert res_json["images"] == []
