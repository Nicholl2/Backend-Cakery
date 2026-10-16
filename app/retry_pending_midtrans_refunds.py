"""Safely retry auto Midtrans refunds that remain locally pending.

The command is read-only by default. ``--apply`` checks Midtrans Status API
first, then retries only when no refund entry exists and the transaction is
still settled. It reuses the original refund_key, amount, and reason.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
from decimal import Decimal
from urllib.parse import quote

import httpx
from sqlalchemy import or_, select

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


async def _load_candidates(limit: int | None) -> list[tuple[Payment, Invoice, RefundTransaction]]:
    async with AsyncSessionLocal() as db:
        stmt = (
            select(Payment, Invoice, RefundTransaction)
            .join(Invoice, Invoice.id == Payment.invoice_id)
            .join(RefundTransaction, RefundTransaction.payment_id == Payment.id)
            .where(
                Payment.payment_status == PaymentStatusEnum.success,
                Payment.pg_transaction_id.is_not(None),
                Payment.pg_order_id.is_not(None),
                RefundTransaction.status == RefundStatusEnum.pending,
                or_(
                    RefundTransaction.initiated_source.like("%:auto"),
                    RefundTransaction.initiated_source.like("%:in_progress"),
                ),
            )
            .order_by(Payment.id, RefundTransaction.id)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return [tuple(row) for row in (await db.execute(stmt)).all()]


async def _status_payload(
    client: httpx.AsyncClient, payment: Payment, invoice: Invoice
) -> tuple[dict | None, str | None]:
    transaction_id = str(payment.pg_transaction_id or "").strip()
    url = f"{settings.midtrans_api_url}/{quote(transaction_id, safe='')}/status"
    try:
        response = await client.get(url)
    except httpx.HTTPError as exc:
        return None, f"Status API request failed: {type(exc).__name__}"
    if response.status_code != 200:
        return None, f"Status API returned HTTP {response.status_code}"
    try:
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("unexpected response shape")
        await _validate_midtrans_payment_payload_for_retry(payment, invoice, payload)
    except (ValueError, TypeError) as exc:
        return None, f"Status API response did not match local payment: {exc}"
    return payload, None


async def _validate_midtrans_payment_payload_for_retry(
    payment: Payment, invoice: Invoice, payload: dict
) -> None:
    transaction_id = str(payload.get("transaction_id") or "").strip()
    order_id = str(payload.get("order_id") or "").strip()
    if transaction_id != payment.pg_transaction_id:
        raise ValueError("transaction_id mismatch")
    if order_id != payment.pg_order_id:
        raise ValueError("order_id mismatch")
    if not invoice.nomor_invoice:
        raise ValueError("invoice missing")
    try:
        remote_amount = Decimal(str(payload.get("gross_amount")))
        local_amount = Decimal(str(payment.jumlah_bayar))
    except Exception as exc:
        raise ValueError("gross_amount missing or invalid") from exc
    if not remote_amount.is_finite() or remote_amount != local_amount:
        raise ValueError("gross_amount mismatch")


async def _process_one(
    client: httpx.AsyncClient,
    payment_id: int,
    refund_id: int,
    *,
    apply: bool,
) -> dict:
    report = {"payment_id": payment_id, "refund_id": refund_id, "eligible": False,
              "result": "not applied" if apply else "dry-run"}
    async with AsyncSessionLocal() as db:
        payment = await db.scalar(
            select(Payment).where(Payment.id == payment_id).with_for_update()
        )
        refund = await db.scalar(
            select(RefundTransaction).where(RefundTransaction.id == refund_id).with_for_update()
        )
        invoice = await db.get(Invoice, payment.invoice_id) if payment else None
        if not payment or not refund or not invoice:
            report["result"] = "payment, refund, or invoice missing"
            return report
        if refund.payment_id != payment.id:
            report["result"] = "refund belongs to another payment"
            return report
        if payment.payment_status != PaymentStatusEnum.success:
            report["result"] = "payment is no longer successful; inspect manually"
            return report
        if refund.status != RefundStatusEnum.pending or not refund.gateway_ref:
            report["result"] = "refund is no longer pending or has no idempotency key"
            return report
        if not refund.initiated_source.endswith((":auto", ":in_progress")):
            report["result"] = "refund is not marked as an automatic Midtrans request"
            return report

        payload, error = await _status_payload(client, payment, invoice)
        if error:
            report["result"] = error
            return report
        report["gateway_status"] = payload.get("transaction_status")
        entries = payload.get("refunds") or []
        report["gateway_refund_entries"] = len(entries) if isinstance(entries, list) else "invalid"
        report["refund_key"] = refund.gateway_ref

        if not isinstance(entries, list):
            report["result"] = "Status API refunds field is invalid"
            return report
        if entries:
            matching = any(
                isinstance(entry, dict) and str(entry.get("refund_key") or "") == refund.gateway_ref
                for entry in entries
            )
            if not matching:
                report["result"] = "Midtrans has refund entries with different keys; inspect before retry"
                return report
            report["eligible"] = True
            if apply:
                try:
                    await _validate_midtrans_payment_payload(db, payment, payload)
                    await complete_refund_notification(db, payment, payload)
                    await db.commit()
                    report["result"] = "existing Midtrans refund reconciled"
                except Exception as exc:
                    await db.rollback()
                    report["result"] = f"refund reconciliation failed: {type(exc).__name__}"
            else:
                report["result"] = "matching Midtrans refund found; would reconcile it"
            return report

        gateway_status = str(payload.get("transaction_status") or "").lower()
        if gateway_status not in {"settlement", "capture"}:
            report["result"] = f"gateway status {gateway_status or 'missing'} is not retryable"
            return report
        report["eligible"] = True
        if not apply:
            report["result"] = "would retry using the same refund_key"
            report["amount"] = str(refund.refund_amount)
            return report

        amount = Decimal(str(refund.refund_amount))
        if amount != amount.to_integral_value():
            report["eligible"] = False
            report["result"] = "refund amount is not an integer IDR value"
            return report
        url = f"{settings.midtrans_api_url}/{quote(payment.pg_transaction_id, safe='')}/refund"
        request_body = {
            "refund_key": refund.gateway_ref,
            "amount": int(amount),
            "reason": refund.reason,
        }
        try:
            response = await client.post(url, json=request_body, headers={
                "Content-Type": "application/json", "Accept": "application/json",
            })
            body = response.json() if response.content else {}
        except (httpx.HTTPError, ValueError) as exc:
            report["result"] = f"retry outcome uncertain; keep the same refund_key: {type(exc).__name__}"
            report["eligible"] = True
            return report

        report["http_status"] = response.status_code
        report["status_code"] = str(body.get("status_code") or "") if isinstance(body, dict) else ""
        if response.status_code in (200, 201) and report["status_code"].startswith("2"):
            if isinstance(body, dict):
                try:
                    await complete_refund_notification(db, payment, body)
                except Exception:
                    # An accepted refund stays pending until a matching signed
                    # notification or a later Status API check confirms it.
                    await db.rollback()
                    report["result"] = "Midtrans accepted retry; local confirmation awaits callback/status reconciliation"
                    return report
            await db.commit()
            report["result"] = "Midtrans accepted retry with original refund_key; awaiting bank confirmation"
        elif response.status_code == 416 or report["status_code"] == "416":
            if refund.initiated_source.endswith(":auto"):
                refund.initiated_source = refund.initiated_source[:-len(":auto")] + ":in_progress"
            await db.commit()
            report["result"] = "Midtrans still processing; reservation kept pending with same key"
        elif response.status_code == 412 or report["status_code"] == "412":
            source = refund.initiated_source
            if source.endswith(":in_progress"):
                source = source[:-len(":in_progress")]
            elif source.endswith(":auto"):
                source = source[:-len(":auto")]
            suffix = ":manual"
            refund.initiated_source = f"{source[:32 - len(suffix)]}{suffix}"
            await db.commit()
            report["result"] = "Midtrans rejected retry; refund remains pending for seller manual payout"
        elif 400 <= response.status_code < 500:
            await db.commit()
            report["result"] = "gateway rejected request; keep pending and investigate configuration/request"
        else:
            await db.commit()
            report["result"] = "gateway error; reservation kept pending with original key for later review"
        return report


async def run(*, apply: bool, limit: int | None) -> int:
    if not settings.midtrans_server_key:
        raise RuntimeError("MIDTRANS_SERVER_KEY is not configured")
    candidates = await _load_candidates(limit)
    auth = base64.b64encode(f"{settings.midtrans_server_key}:".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth}",
        "Accept": "application/json",
    }
    reports = []
    async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
        for payment, _invoice, refund in candidates:
            reports.append(await _process_one(
                client, payment.id, refund.id, apply=apply,
            ))
    print(json.dumps({
        "mode": "apply" if apply else "dry-run",
        "candidate_count": len(candidates),
        "eligible_count": sum(bool(item["eligible"]) for item in reports),
        "reports": reports,
    }, indent=2, default=str))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Retry pending automatic Midtrans refunds with the original idempotency key."
    )
    parser.add_argument("--apply", action="store_true", help="send eligible retries to Midtrans")
    parser.add_argument("--limit", type=int, help="limit the number of pending refunds checked")
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
        parser.exit(1, f"Midtrans refund retry failed: {type(exc).__name__}: {exc}\n")


if __name__ == "__main__":
    main()
