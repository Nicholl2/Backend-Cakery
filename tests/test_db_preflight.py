"""Unit tests for app.db_preflight check logic."""
import pytest
from decimal import Decimal
from types import SimpleNamespace
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.database import Base
from app.db_preflight import (
    _stock_name_index_is_compatible,
    _normalized_index_expression,
    _validate_stock_ledger,
    run_preflight_checks,
)


@pytest.mark.asyncio
async def test_preflight_rejects_non_postgresql_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    is_valid, errors, notices = await run_preflight_checks(engine)
    await engine.dispose()

    assert is_valid is False
    assert any("only PostgreSQL is supported" in err for err in errors)


@pytest.mark.asyncio
async def test_preflight_rejects_unverified_adoption_revision():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    is_valid, errors, notices = await run_preflight_checks(engine, "head")
    await engine.dispose()

    assert not is_valid
    assert errors == ["Unsupported adoption revision: head"]
    assert notices == []


def test_stock_name_index_accepts_postgres_deparsed_migration_expression():
    row = SimpleNamespace(
        access_method="btree", is_unique=True, is_valid=True, is_ready=True,
        has_no_predicate=True, key_count=1, total_count=1,
        expression="lower(btrim(nama_item))",
    )
    assert _normalized_index_expression(row.expression) == "lower(trim(nama_item))"
    assert _stock_name_index_is_compatible(row)


@pytest.mark.parametrize("overrides", [
    {"is_unique": False},
    {"expression": "lower(nama_item)"},
    {"access_method": "gin"},
    {"has_no_predicate": False},
    {"is_valid": False},
])
def test_stock_name_index_rejects_incompatible_definitions(overrides):
    values = dict(
        access_method="btree", is_unique=True, is_valid=True, is_ready=True,
        has_no_predicate=True, key_count=1, total_count=1,
        expression="lower(btrim(nama_item))",
    )
    values.update(overrides)
    assert not _stock_name_index_is_compatible(SimpleNamespace(**values))


def test_stock_name_index_rejects_missing_or_unverifiable_definition():
    assert not _stock_name_index_is_compatible(None)
    assert not _stock_name_index_is_compatible(SimpleNamespace(
        access_method="btree", is_unique=True, is_valid=True, is_ready=True,
        has_no_predicate=True, key_count=1, total_count=1, expression=None,
    ))


def test_stock_ledger_replays_exact_quantity_and_weighted_average_cost():
    stock = SimpleNamespace(id=1, nama_item="Flour", stok_tersedia=Decimal("15.00"),
                            harga_per_satuan=Decimal("4.0000"))
    movements = [
        SimpleNamespace(id=1, movement_type="OPENING_BALANCE", quantity=Decimal("10.0000"),
                        unit_cost=Decimal("2.0000")),
        SimpleNamespace(id=2, movement_type="PURCHASE", quantity=Decimal("5.0000"),
                        unit_cost=Decimal("8.0000")),
    ]
    assert _validate_stock_ledger(stock, movements) == []


def test_stock_ledger_matches_adjustment_rounding_used_by_service():
    stock = SimpleNamespace(id=2, nama_item="Salt", stok_tersedia=Decimal("2.00"),
                            harga_per_satuan=Decimal("0.0000"))
    movements = [
        SimpleNamespace(id=1, movement_type="OPENING_BALANCE", quantity=Decimal("1.0000"),
                        unit_cost=Decimal("0.0000")),
        SimpleNamespace(id=2, movement_type="ADJUSTMENT_IN", quantity=Decimal("1.0000"),
                        unit_cost=Decimal("0.0001")),
    ]
    assert _validate_stock_ledger(stock, movements) == []


@pytest.mark.parametrize(("stock_qty", "opening_qty", "opening_cost", "expected"), [
    ("10.00", "9.0000", "2.0000", "ledger quantity"),
    ("10.00", "10.0000", "3.0000", "reconstructed ledger unit cost"),
    ("10.00", "-10.0000", "2.0000", "negative OPENING_BALANCE quantity"),
    ("-1.00", "-1.0000", "2.0000", "negative or non-finite master stock/price"),
])
def test_stock_ledger_rejects_invalid_opening_values(stock_qty, opening_qty, opening_cost, expected):
    stock = SimpleNamespace(id=7, nama_item="Butter", stok_tersedia=Decimal(stock_qty),
                            harga_per_satuan=Decimal("2.0000"))
    movement = SimpleNamespace(id=1, movement_type="OPENING_BALANCE", quantity=Decimal(opening_qty),
                               unit_cost=Decimal(opening_cost))
    errors = _validate_stock_ledger(stock, [movement])
    assert any(expected in error for error in errors)


def test_stock_ledger_rejects_duplicate_opening_movements():
    stock = SimpleNamespace(id=8, nama_item="Sugar", stok_tersedia=Decimal("20.00"),
                            harga_per_satuan=Decimal("1.0000"))
    movement = SimpleNamespace(id=1, movement_type="OPENING_BALANCE", quantity=Decimal("10.0000"),
                               unit_cost=Decimal("1.0000"))
    second = SimpleNamespace(id=2, movement_type="OPENING_BALANCE", quantity=Decimal("10.0000"),
                             unit_cost=Decimal("1.0000"))
    errors = _validate_stock_ledger(stock, [movement, second])
    assert any("duplicate or out-of-order OPENING_BALANCE" in error for error in errors)
