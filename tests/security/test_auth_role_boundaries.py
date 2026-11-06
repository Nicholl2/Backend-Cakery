from unittest.mock import AsyncMock

import httpx
import pytest

from app.api.dependencies import get_current_user_id, get_db
from app.core.security import create_access_token
from app.main import app


async def _empty_db_dependency():
    yield None


@pytest.mark.asyncio
async def test_buyer_token_cannot_access_internal_me_routes_on_id_collision(monkeypatch):
    from app.api.dependencies import user_repo

    active_lookup = AsyncMock(return_value=True)
    monkeypatch.setattr(user_repo, "is_user_active", active_lookup)
    app.dependency_overrides[get_db] = _empty_db_dependency
    buyer_token = create_access_token(
        user_id=1, role_level=0, username="buyer@example.test", role="buyer"
    )

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            for method, path, kwargs in (
                ("GET", "/users/me", {}),
                ("PATCH", "/users/me", {"json": {"username": "changed"}}),
                (
                    "POST",
                    "/users/me/change-password",
                    {
                        "json": {
                            "old_password": "old-password",
                            "new_password": "new-password-123",
                        }
                    },
                ),
            ):
                response = await client.request(
                    method,
                    path,
                    headers={"Authorization": f"Bearer {buyer_token}"},
                    **kwargs,
                )
                assert response.status_code == 403

        active_lookup.assert_not_awaited()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_cost_and_price_history_routes_require_admin_or_owner(monkeypatch):
    from app.services import product_service

    monkeypatch.setattr(
        product_service,
        "get_pricing_breakdown",
        AsyncMock(return_value={
            "product_id": 1,
            "nama_produk": "Brownies",
            "hpp": "10000",
            "harga_jual": "20000",
            "margin_persen": 50,
            "warning_below_hpp": False,
            "breakdown": [],
        }),
    )
    monkeypatch.setattr(product_service, "get_price_history", AsyncMock(return_value=[]))
    app.dependency_overrides[get_db] = _empty_db_dependency
    buyer_token = create_access_token(
        user_id=1, role_level=0, username="buyer@example.test", role="buyer"
    )
    admin_token = create_access_token(user_id=2, role_level=2, username="admin")

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            for path in (
                "/products/internal",
                "/products/internal/1",
                "/products/1/pricing",
                "/products/1/price-history",
                "/pricing/product/1",
            ):
                response = await client.get(
                    path,
                    headers={"Authorization": f"Bearer {buyer_token}"},
                )
                assert response.status_code == 403

            headers = {"Authorization": f"Bearer {admin_token}"}
            pricing_response = await client.get(
                "/products/1/pricing", headers=headers
            )
            history_response = await client.get(
                "/products/1/price-history", headers=headers
            )
            assert pricing_response.status_code == 200
            assert history_response.status_code == 200
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_staff_can_adjust_inventory_with_audited_movement(monkeypatch):
    from app.api.dependencies import get_current_user_id
    from app.services import stock_service

    monkeypatch.setattr(
        stock_service,
        "adjust_stock",
        AsyncMock(return_value={
            "id": 1,
            "nama_item": "Tepung",
            "satuan": "gram",
            "kategori": "bahan_baku",
            "harga_per_satuan": "10",
            "stok_tersedia": "12",
            "alert_min_stok": "1",
            "version": 2,
        }),
    )
    app.dependency_overrides[get_db] = _empty_db_dependency
    app.dependency_overrides[get_current_user_id] = lambda: 3
    staff_token = create_access_token(user_id=3, role_level=3, username="staff")

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/stock/1/adjustments",
                json={
                    "quantity_difference": "2",
                    "reason": "physical count correction",
                    "unit_cost": "0",
                },
                headers={"Authorization": f"Bearer {staff_token}"},
            )
        assert response.status_code == 201
        stock_service.adjust_stock.assert_awaited_once()
    finally:
        app.dependency_overrides.clear()
