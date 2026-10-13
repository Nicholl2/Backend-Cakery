"""Restricted local operator commands for email outbox inspection and retry."""
import argparse
import asyncio
import getpass
import hmac
import json
import logging
import sys
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.email_outbox import EmailOutbox, EmailOutboxAudit

logger = logging.getLogger(__name__)


def _authorize() -> str:
    expected = settings.email_outbox_admin_token
    if not expected:
        raise PermissionError("EMAIL_OUTBOX_ADMIN_TOKEN is not configured")
    supplied = getpass.getpass("Outbox operator token: ")
    if not hmac.compare_digest(supplied, expected):
        raise PermissionError("Invalid operator token")
    return getpass.getuser()[:100]


def _age_seconds(value: datetime | None) -> int | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - value).total_seconds()))


async def _status() -> int:
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(EmailOutbox.status, func.count(EmailOutbox.id), func.min(EmailOutbox.created_at))
            .where(EmailOutbox.status.in_(("pending", "sending", "failed")))
            .group_by(EmailOutbox.status)
        )).all()
        oldest_event = await db.scalar(select(func.min(EmailOutbox.created_at)))
        expired_sending_count, oldest_expired_lease = (await db.execute(
            select(func.count(EmailOutbox.id), func.min(EmailOutbox.lease_expires_at)).where(
                EmailOutbox.status == "sending",
                EmailOutbox.lease_expires_at.is_not(None),
                EmailOutbox.lease_expires_at <= now,
            )
        )).one()
    by_status = {status: (count, oldest) for status, count, oldest in rows}
    pending_count, oldest_pending = by_status.get("pending", (0, None))
    sending_count, oldest_sending = by_status.get("sending", (0, None))
    failed_count, oldest_failed = by_status.get("failed", (0, None))
    print(json.dumps({
        "pending": pending_count, "sending": sending_count, "failed": failed_count,
        "oldest_pending_age_seconds": _age_seconds(oldest_pending),
        "oldest_sending_age_seconds": _age_seconds(oldest_sending),
        "expired_sending": expired_sending_count,
        "oldest_expired_lease_age_seconds": _age_seconds(oldest_expired_lease),
        "oldest_failed_age_seconds": _age_seconds(oldest_failed),
        "oldest_event_age_seconds": _age_seconds(oldest_event),
    }, indent=2))
    return 0


async def _failed(event_id: int | None, limit: int) -> int:
    async with AsyncSessionLocal() as db:
        query = select(EmailOutbox).where(EmailOutbox.status == "failed").order_by(EmailOutbox.id.desc()).limit(limit)
        if event_id is not None:
            query = select(EmailOutbox).where(EmailOutbox.status == "failed", EmailOutbox.id == event_id)
        events = (await db.execute(query)).scalars().all()
    # This command is local operator-only; never expose these fields through API routes.
    for event in events:
        print(json.dumps({
            "id": event.id, "event_key": event.event_key, "event_type": event.event_type,
            "recipient": event.recipient, "payload": json.loads(event.payload_json),
            "attempts": event.attempts, "last_error": event.last_error,
            "created_at": event.created_at.isoformat() if event.created_at else None,
        }, ensure_ascii=False))
    return 0


async def _retry(event_id: int, reason: str, actor: str) -> int:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(EmailOutbox).where(EmailOutbox.id == event_id).with_for_update()
        )
        event = result.scalars().first()
        if event is None or event.status != "failed":
            print("No failed event found; no changes made.", file=sys.stderr)
            await db.rollback()
            return 2
        event.status = "pending"
        event.attempts = 0
        event.next_attempt_at = datetime.now(timezone.utc)
        event.lease_token = None
        event.lease_expires_at = None
        event.last_error = None
        db.add(EmailOutboxAudit(event_id=event.id, action="retry", actor=actor, reason=reason))
        await db.commit()
    logger.info("Email outbox retry authorized (event_id=%s actor=%s)", event_id, actor)
    print(f"Event {event_id} returned to pending; retry recorded.")
    return 0


async def _run(args: argparse.Namespace, actor: str) -> int:
    if args.command == "status":
        return await _status()
    if args.command == "failed":
        return await _failed(args.id, args.limit)
    return await _retry(args.id, args.reason, actor)


def main() -> None:
    parser = argparse.ArgumentParser(description="Restricted email outbox operations")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="show aggregate backlog counts and event ages")
    failed = sub.add_parser("failed", help="show failed event details (sensitive; local operator use only)")
    failed.add_argument("--id", type=int)
    failed.add_argument("--limit", type=int, default=20)
    retry = sub.add_parser("retry-failed", help="retry one failed event and record an audit entry")
    retry.add_argument("--id", type=int, required=True)
    retry.add_argument("--reason", required=True)
    args = parser.parse_args()
    if args.command == "failed" and args.limit < 1:
        parser.error("--limit must be positive")
    try:
        actor = _authorize()
    except PermissionError as exc:
        parser.error(str(exc))
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(asyncio.run(_run(args, actor)))


if __name__ == "__main__":
    main()
