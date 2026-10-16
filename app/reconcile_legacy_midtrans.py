"""Safely reconcile legacy Midtrans payments missing their persisted order ID.

The default mode is read-only. Pass ``--apply`` only after reviewing the
gateway-verified report; eligible rows are backfilled and refreshed through
the normal payment service.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

import httpx
from sqlalchemy import select

from app.core.config import settings
from app.core.database import AsyncSessionLocal, engine
from app.models.order import Invoice
from app.models.payment import Payment, PaymentStatusEnum
from app.services.payment_service import refresh_if_pending


def _order_id_belongs_to_invoice(order_id: str, invoice_number: str) -> bool:
    """Accept only the invoice's legacy ID or the current charge-ID format."""
    return order_id == invoice_number or order_id.startswith(f"{invoice_number}-PAY-")


async def _fetch_gateway_status(
    client: httpx.AsyncClient, payment: Payment, invoice: Invoice
) -> dict:
    transaction_id = str(payment.pg_transaction_id or "").strip()
    result = {
        "payment_id": payment.id,
        "invoice": invoice.nomor_invoice,
        "transaction_id": transaction_id,
        "eligible": False,
        "reason": None,
        "gateway_status": None,
        "gateway_order_id": None,
    }
    if not transaction_id:
        result["reason"] = "missing local transaction_id"
        return result

    url = f"{settings.midtrans_api_url}/{quote(transaction_id, safe='')}/status"
    try:
        response = await client.get(url)
    except httpx.HTTPError as exc:
        result["reason"] = f"gateway request failed: {type(exc).__name__}"
        return result
    if response.status_code != 200:
        result["reason"] = f"gateway returned HTTP {response.status_code}"
        return result
    try:
        payload = response.json()
    except ValueError:
        result["reason"] = "gateway returned invalid JSON"
        return result
    if not isinstance(payload, dict):
        result["reason"] = "gateway returned an unexpected response shape"
        return result

    remote_transaction_id = str(payload.get("transaction_id") or "").strip()
    remote_order_id = str(payload.get("order_id") or "").strip()
    result["gateway_status"] = str(payload.get("transaction_status") or "") or None
    result["gateway_order_id"] = remote_order_id or None

    if remote_transaction_id != transaction_id:
        result["reason"] = "gateway transaction_id does not match local record"
        return result
    try:
        remote_amount = Decimal(str(payload.get("gross_amount")))
        local_amount = Decimal(str(payment.jumlah_bayar))
    except (InvalidOperation, TypeError, ValueError):
        result["reason"] = "gateway gross_amount is missing or invalid"
        return result
    if not remote_amount.is_finite() or remote_amount != local_amount:
        result["reason"] = "gateway gross_amount does not match local payment"
        return result
    if not remote_order_id or not _order_id_belongs_to_invoice(
        remote_order_id, invoice.nomor_invoice
    ):
        result["reason"] = "gateway order_id cannot be tied to this invoice"
        return result
    if len(remote_order_id) > 100:
        result["reason"] = "gateway order_id exceeds the current database column length"
        return result

    result["eligible"] = True
    result["reason"] = "transaction, amount, and invoice reference match"
    return result


async def _load_candidates(limit: int | None) -> list[tuple[Payment, Invoice]]:
    async with AsyncSessionLocal() as db:
        stmt = (
            select(Payment, Invoice)
            .join(Invoice, Invoice.id == Payment.invoice_id)
            .where(
                Payment.payment_status == PaymentStatusEnum.pending,
                Payment.pg_order_id.is_(None),
                Payment.pg_transaction_id.is_not(None),
            )
            .order_by(Payment.created_at, Payment.id)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        rows = (await db.execute(stmt)).all()
        return [(payment, invoice) for payment, invoice in rows]


async def _apply_verified_reference(report: dict) -> dict:
    """Backfill one verified reference, then refresh through normal logic."""
    async with AsyncSessionLocal() as db:
        payment = await db.scalar(
            select(Payment)
            .where(Payment.id == report["payment_id"])
            .with_for_update()
        )
        if not payment:
            report["applied"] = False
            report["apply_result"] = "payment disappeared"
            return report
        if payment.payment_status != PaymentStatusEnum.pending:
            report["applied"] = False
            report["apply_result"] = "payment status changed during reconciliation"
            return report
        if payment.pg_order_id:
            report["applied"] = False
            report["apply_result"] = "pg_order_id was populated during reconciliation"
            return report
        if payment.pg_transaction_id != report["transaction_id"]:
            report["applied"] = False
            report["apply_result"] = "transaction_id changed during reconciliation"
            return report

        collision = await db.scalar(
            select(Payment.id).where(
                Payment.pg_order_id == report["gateway_order_id"],
                Payment.id != payment.id,
            ).limit(1)
        )
        if collision is not None:
            report["applied"] = False
            report["apply_result"] = f"order_id already belongs to payment {collision}"
            return report

        payment.pg_order_id = report["gateway_order_id"]
        await db.commit()

        # The normal service re-fetches authoritative status, validates all
        # references again, and applies the standard state transitions.
        refreshed = await refresh_if_pending(db, payment)
        report["applied"] = True
        report["apply_result"] = "reference backfilled and status refreshed"
        report["local_status"] = refreshed.payment_status.value
        return report


async def run(*, apply: bool, limit: int | None) -> int:
    if not settings.midtrans_server_key:
        raise RuntimeError("MIDTRANS_SERVER_KEY is not configured")

    candidates = await _load_candidates(limit)
    auth = base64.b64encode(f"{settings.midtrans_server_key}:".encode()).decode()
    headers = {"Authorization": f"Basic {auth}", "Accept": "application/json"}
    semaphore = asyncio.Semaphore(5)

    async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
        async def fetch(payment: Payment, invoice: Invoice) -> dict:
            async with semaphore:
                return await _fetch_gateway_status(client, payment, invoice)

        reports = await asyncio.gather(
            *(fetch(payment, invoice) for payment, invoice in candidates)
        )

    if apply:
        for report in reports:
            if report["eligible"]:
                await _apply_verified_reference(report)

    print(json.dumps({
        "mode": "apply" if apply else "dry-run",
        "candidate_count": len(candidates),
        "eligible_count": sum(bool(item["eligible"]) for item in reports),
        "reports": reports,
    }, indent=2, default=str))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify and optionally reconcile legacy Midtrans payment references."
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="backfill only gateway-verified references and refresh their statuses",
    )
    parser.add_argument("--limit", type=int, help="limit the number of pending payments checked")
    args = parser.parse_args()
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be a positive integer")
    try:
        asyncio.run(run(apply=args.apply, limit=args.limit))
    finally:
        asyncio.run(engine.dispose())


if __name__ == "__main__":
    main()
