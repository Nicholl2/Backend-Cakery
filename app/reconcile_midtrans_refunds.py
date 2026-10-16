"""Reconcile pending or incomplete Midtrans refunds from the Status API.

The default mode is read-only. Use ``--apply`` only after reviewing the report.
Only refund entries returned by Midtrans are written; this tool never infers a
refund amount or completion from the local payment status alone.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

import httpx
from sqlalchemy import and_, exists, func, or_, select

from app.core.config import settings
from app.core.database import AsyncSessionLocal, engine
from app.models.order import Invoice
from app.models.payment import (
    Payment,
    PaymentStatusEnum,
    RefundStatusEnum,
    RefundTransaction,
)
from app.services.payment_service import (
    _validate_midtrans_payment_payload,
    complete_refund_notification,
)


async def _load_candidates(limit: int | None) -> list[tuple[Payment, Invoice]]:
    completed_amount = (
        select(func.coalesce(func.sum(RefundTransaction.refund_amount), 0))
        .where(
            RefundTransaction.payment_id == Payment.id,
            RefundTransaction.status == RefundStatusEnum.completed,
        )
        .scalar_subquery()
    )
    has_pending_refund = exists(
        select(RefundTransaction.id).where(
            RefundTransaction.payment_id == Payment.id,
            RefundTransaction.status == RefundStatusEnum.pending,
        )
    )
    refunded_without_full_ledger = and_(
        Payment.payment_status == PaymentStatusEnum.refunded,
        completed_amount < Payment.jumlah_bayar,
    )
    async with AsyncSessionLocal() as db:
        stmt = (
            select(Payment, Invoice)
            .join(Invoice, Invoice.id == Payment.invoice_id)
            .where(
                Payment.pg_transaction_id.is_not(None),
                Payment.pg_order_id.is_not(None),
                or_(has_pending_refund, refunded_without_full_ledger),
            )
            .order_by(Payment.id)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return [(payment, invoice) for payment, invoice in (await db.execute(stmt)).all()]


async def _fetch_status(
    client: httpx.AsyncClient, payment: Payment, invoice: Invoice
) -> tuple[dict, dict | None]:
    report = {
        "payment_id": payment.id,
        "invoice": invoice.nomor_invoice,
        "local_payment_status": payment.payment_status.value,
        "eligible": False,
        "reason": None,
        "gateway_status": None,
        "refund_entries_count": 0,
        "confirmed_entries_count": 0,
    }
    transaction_id = str(payment.pg_transaction_id or "").strip()
    url = f"{settings.midtrans_api_url}/{quote(transaction_id, safe='')}/status"
    try:
        response = await client.get(url)
    except httpx.HTTPError as exc:
        report["reason"] = f"gateway request failed: {type(exc).__name__}"
        return report, None
    if response.status_code != 200:
        report["reason"] = f"gateway returned HTTP {response.status_code}"
        return report, None
    try:
        payload = response.json()
    except ValueError:
        report["reason"] = "gateway returned invalid JSON"
        return report, None
    if not isinstance(payload, dict):
        report["reason"] = "gateway returned an unexpected response shape"
        return report, None

    remote_transaction_id = str(payload.get("transaction_id") or "").strip()
    remote_order_id = str(payload.get("order_id") or "").strip()
    report["gateway_status"] = payload.get("transaction_status")
    if remote_transaction_id != transaction_id:
        report["reason"] = "gateway transaction_id does not match local payment"
        return report, None
    if not payment.pg_order_id or remote_order_id != payment.pg_order_id:
        report["reason"] = "gateway order_id does not match persisted Midtrans reference"
        return report, None
    try:
        gateway_amount = Decimal(str(payload.get("gross_amount")))
        local_amount = Decimal(str(payment.jumlah_bayar))
    except (InvalidOperation, TypeError, ValueError):
        report["reason"] = "gateway gross_amount is missing or invalid"
        return report, None
    if not gateway_amount.is_finite() or gateway_amount != local_amount:
        report["reason"] = "gateway gross_amount does not match local payment"
        return report, None

    refund_entries = payload.get("refunds") or []
    if not isinstance(refund_entries, list):
        report["reason"] = "gateway refunds field has an unexpected shape"
        return report, None
    valid_entries = [
        item for item in refund_entries
        if isinstance(item, dict) and item.get("refund_key") and item.get("refund_amount") is not None
    ]
    report["refund_entries_count"] = len(valid_entries)
    report["confirmed_entries_count"] = sum(bool(item.get("bank_confirmed_at")) for item in valid_entries)
    report["eligible"] = True
    report["reason"] = "transaction, order reference, and amount match Midtrans"
    return report, payload


async def _apply_status(payment_id: int, expected_status: str, payload: dict) -> dict:
    async with AsyncSessionLocal() as db:
        payment = await db.scalar(
            select(Payment).where(Payment.id == payment_id).with_for_update()
        )
        if not payment:
            return {"applied": False, "apply_result": "payment disappeared"}
        if payment.payment_status.value != expected_status:
            return {"applied": False, "apply_result": "payment status changed during reconciliation"}
        try:
            await _validate_midtrans_payment_payload(db, payment, payload)
            await complete_refund_notification(db, payment, payload)
            await db.commit()
        except Exception as exc:
            await db.rollback()
            return {"applied": False, "apply_result": f"not applied: {type(exc).__name__}"}
        return {"applied": True, "apply_result": "gateway refund entries reconciled"}


async def run(*, apply: bool, limit: int | None) -> int:
    if not settings.midtrans_server_key:
        raise RuntimeError("MIDTRANS_SERVER_KEY is not configured")
    candidates = await _load_candidates(limit)
    auth = base64.b64encode(f"{settings.midtrans_server_key}:".encode()).decode()
    headers = {"Authorization": f"Basic {auth}", "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
        reports = []
        for payment, invoice in candidates:
            report, payload = await _fetch_status(client, payment, invoice)
            report["applied"] = False
            report["apply_result"] = "dry-run" if not apply else "not applied"
            if apply and report["eligible"] and payload and payload.get("refunds"):
                entries = payload["refunds"]
                # For a legacy payment already marked refunded, only bank-
                # confirmed Status API entries may repair the missing ledger.
                # Unconfirmed entries must not downgrade its local state.
                if payment.payment_status == PaymentStatusEnum.refunded:
                    entries = [
                        item for item in entries
                        if isinstance(item, dict) and item.get("bank_confirmed_at")
                    ]
                if entries:
                    result = await _apply_status(
                        payment.id,
                        payment.payment_status.value,
                        {**payload, "refunds": entries},
                    )
                    report.update(result)
                else:
                    report["apply_result"] = "no bank-confirmed refund entries to apply"
            elif apply and report["eligible"]:
                report["apply_result"] = "no refund entries returned by Midtrans"
            reports.append(report)
    print(json.dumps({
        "mode": "apply" if apply else "dry-run",
        "candidate_count": len(candidates),
        "reports": reports,
    }, indent=2, default=str))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify and optionally reconcile Midtrans refund details."
    )
    parser.add_argument("--apply", action="store_true", help="write gateway-verified refund entries")
    parser.add_argument("--limit", type=int, help="limit the number of payments checked")
    args = parser.parse_args()
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be a positive integer")
    if args.apply and args.limit is not None:
        parser.error("--apply processes the complete candidate set; omit --limit")

    async def run_and_dispose() -> None:
        try:
            await run(apply=args.apply, limit=args.limit)
        finally:
            await engine.dispose()

    try:
        asyncio.run(run_and_dispose())
    except Exception as exc:
        parser.exit(1, f"Refund reconciliation failed: {type(exc).__name__}: {exc}\n")


if __name__ == "__main__":
    main()
