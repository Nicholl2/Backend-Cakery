import asyncio
import hashlib
import html
import json
import logging
import argparse
import uuid
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.email_outbox import EmailOutbox
from app.models.email_sender_settings import EmailSenderSettings

logger = logging.getLogger(__name__)


class EmailBatchDeliveryError(RuntimeError):
    """Raised after a bounded batch records one or more failed deliveries."""


def _message_id(event_key: str) -> str:
    digest = hashlib.sha256(event_key.encode("utf-8")).hexdigest()
    return f"<{digest}@toti-cakery>"


def _render_email(event: EmailOutbox) -> tuple[str, str]:
    data = json.loads(event.payload_json)
    sender_name = getattr(event, "_sender_name", None) or settings.smtp_from_name
    order_number = html.escape(str(data.get("order_number", "")))
    name = html.escape(str(data.get("customer_name", "Pelanggan")))
    total = html.escape(str(data.get("total", "")))
    if event.event_type == "order_created":
        subject = f"Pesanan baru {order_number} — {sender_name}"
        body = f"<p>Pesanan baru dari {name}.</p><p>Nomor: {order_number}</p><p>Total: Rp {total}</p>"
    elif event.event_type == "order_status_updated":
        status = html.escape(str(data.get("status", "")))
        subject = f"Status pesanan {order_number} diperbarui"
        body = f"<p>Halo {name}, status pesanan {order_number} sekarang <strong>{status}</strong>.</p>"
    elif event.event_type == "order_overdue":
        subject = f"Pesanan {order_number} melewati tanggal pemenuhan"
        body = f"<p>Pesanan {order_number} untuk {name} melewati tanggal pemenuhan yang dijadwalkan. Hubungi pelanggan untuk menyepakati tindak lanjut.</p>"
    elif event.event_type == "payment_status_updated":
        payment_status = html.escape(str(data.get("payment_status", "")))
        subject = f"Pembayaran pesanan {order_number} diperbarui"
        body = f"<p>Halo {name}, status pembayaran pesanan {order_number}: <strong>{payment_status}</strong>.</p>"
    else:
        refund_status = html.escape(str(data.get("refund_status", "completed")))
        amount = html.escape(str(data.get("amount", "")))
        subject = f"Pembaruan refund pesanan {order_number}"
        body = f"<p>Halo {name}, refund pesanan {order_number} berstatus <strong>{refund_status}</strong>.</p><p>Nominal: Rp {amount}</p>"
    return subject, f"<html><body style=\"font-family:Arial,sans-serif\"><h2>{html.escape(sender_name)}</h2>{body}</body></html>"


async def _send(event: EmailOutbox) -> None:
    sender_email = getattr(event, "_sender_email", None) or settings.smtp_from_email
    sender_name = getattr(event, "_sender_name", None) or settings.smtp_from_name
    if not settings.smtp_host or not sender_email:
        raise RuntimeError("SMTP is not configured")
    import aiosmtplib

    subject, body = _render_email(event)
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{sender_name} <{sender_email}>"
    msg["To"] = event.recipient
    msg["Message-ID"] = _message_id(event.event_key)
    msg.attach(MIMEText(body, "html", "utf-8"))
    await aiosmtplib.send(
        msg, hostname=settings.smtp_host, port=settings.smtp_port,
        username=settings.smtp_username or None, password=settings.smtp_password or None,
        start_tls=settings.smtp_use_tls,
        timeout=min(120, max(1, settings.email_outbox_lease_seconds // 2)),
    )


def _claimable(now: datetime):
    return or_(
        and_(EmailOutbox.status == "pending", EmailOutbox.next_attempt_at <= now),
        and_(EmailOutbox.status == "sending", EmailOutbox.lease_expires_at <= now),
    )


async def _claim_email(db: AsyncSession) -> tuple[EmailOutbox, str] | None:
    """Claim atomically and commit before any network operation is started."""
    now = datetime.now(timezone.utc)
    candidate = await db.execute(
        select(EmailOutbox.id, EmailOutbox.attempts).where(_claimable(now)).order_by(EmailOutbox.id).limit(1)
        .with_for_update(skip_locked=True)
    )
    row = candidate.first()
    if row is None:
        await db.rollback()
        return None
    event_id, prior_attempts = row

    if prior_attempts >= settings.email_outbox_max_attempts:
        failed = await db.execute(
            update(EmailOutbox).where(EmailOutbox.id == event_id, _claimable(now)).values(
                status="failed", lease_token=None, lease_expires_at=None,
                last_error="Worker lease expired after the maximum delivery attempts; delivery outcome is unknown.",
            )
        )
        await db.commit()
        if failed.rowcount == 1:
            logger.warning("Email outbox lease exhausted (id=%s, attempts=%s)", event_id, prior_attempts)
        return None

    token = uuid.uuid4().hex
    lease_expires = now + timedelta(seconds=settings.email_outbox_lease_seconds)
    claimed = await db.execute(
        update(EmailOutbox).where(EmailOutbox.id == event_id, _claimable(now)).values(
            status="sending", attempts=EmailOutbox.attempts + 1,
            lease_token=token, lease_expires_at=lease_expires,
        ).returning(EmailOutbox).execution_options(populate_existing=True)
    )
    event = claimed.scalars().first()
    if event is None:
        await db.rollback()
        return None
    await db.commit()
    return event, token


async def _finish_success(db: AsyncSession, event_id: int, token: str) -> bool:
    result = await db.execute(
        update(EmailOutbox).where(
            EmailOutbox.id == event_id, EmailOutbox.status == "sending", EmailOutbox.lease_token == token,
        ).values(
            status="sent", sent_at=datetime.now(timezone.utc), last_error=None,
            lease_token=None, lease_expires_at=None,
        )
    )
    await db.commit()
    return result.rowcount == 1


async def _finish_failure(db: AsyncSession, event: EmailOutbox, token: str, exc: Exception) -> bool:
    attempts = event.attempts
    values = {
        "attempts": attempts,
        "last_error": str(exc)[:1000],
        "lease_token": None,
        "lease_expires_at": None,
    }
    if attempts >= settings.email_outbox_max_attempts:
        values["status"] = "failed"
    else:
        values["status"] = "pending"
        values["next_attempt_at"] = datetime.now(timezone.utc) + timedelta(
            seconds=min(3600, 5 * (2 ** (attempts - 1)))
        )
    result = await db.execute(
        update(EmailOutbox).where(
            EmailOutbox.id == event.id, EmailOutbox.status == "sending", EmailOutbox.lease_token == token,
        ).values(**values)
    )
    await db.commit()
    if result.rowcount == 1:
        logger.warning("Email outbox delivery failed (id=%s, attempt=%s)", event.id, attempts)
    return result.rowcount == 1


async def _process_one_email(db: AsyncSession) -> tuple[bool, bool | None]:
    """Return (did_work, delivery_succeeded); None means there was no event."""
    sender_factory = async_sessionmaker(bind=db.bind, class_=AsyncSession, expire_on_commit=False)
    async with sender_factory() as sender_db:
        sender = await sender_db.get(EmailSenderSettings, 1)
    sender_email = sender.from_email if sender else None
    sender_name = sender.from_name if sender else None
    claimed = await _claim_email(db)
    if claimed is None:
        return False, None
    event, token = claimed
    if sender_email:
        event._sender_email = sender_email
        event._sender_name = sender_name
    try:
        await _send(event)
    except Exception as exc:
        await _finish_failure(db, event, token, exc)
        return True, False
    if not await _finish_success(db, event.id, token):
        logger.warning("Email outbox lease lost before success was recorded (id=%s)", event.id)
        return True, False
    return True, True


async def process_one_email(db: AsyncSession) -> bool:
    """Claim, send outside transactions, then persist the outcome."""
    did_work, _ = await _process_one_email(db)
    return did_work


async def run_email_worker(poll_seconds: int | None = None) -> None:
    """Standalone polling worker: python -m app.services.email_worker"""
    interval = poll_seconds or settings.email_outbox_poll_seconds
    while True:
        try:
            async with AsyncSessionLocal() as db:
                did_work = await process_one_email(db)
            if not did_work:
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Email outbox worker iteration failed")
            await asyncio.sleep(interval)


async def run_email_batch(limit: int) -> int:
    """Process at most ``limit`` events, then exit for scheduled runtimes."""
    processed = 0
    failures = 0
    for _ in range(limit):
        async with AsyncSessionLocal() as db:
            did_work, delivered = await _process_one_email(db)
        if not did_work:
            break
        processed += 1
        if delivered is False:
            failures += 1
    if failures:
        raise EmailBatchDeliveryError(f"{failures} email delivery attempt(s) failed; outbox retry was recorded")
    return processed


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Toti Cakery email outbox worker")
    parser.add_argument("--once", action="store_true", help="process one bounded batch and exit")
    parser.add_argument("--limit", type=int, default=20, help="maximum events in --once mode")
    args = parser.parse_args()
    if args.limit < 1 or args.limit > 500:
        parser.error("--limit must be between 1 and 500")
    if args.once:
        try:
            count = asyncio.run(run_email_batch(args.limit))
        except EmailBatchDeliveryError as exc:
            logger.error("Email outbox batch incomplete: %s", exc)
            raise SystemExit(1) from None
        logger.info("Email outbox batch complete (processed=%s, limit=%s)", count, args.limit)
    else:
        asyncio.run(run_email_worker())


if __name__ == "__main__":
    main()
