import re
from dataclasses import dataclass
from email.utils import formataddr

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.email_sender_settings import EmailSenderSettings

_EMAIL_PATTERN = re.compile(r'^[^@\s,;<>"\\]+@[^@\s,;<>"\\]+\.[^@\s,;<>"\\]+$')
_CONTROL_CHARS_PATTERN = re.compile(r"[\x00-\x1f\x7f\x85\u2028\u2029]")


@dataclass(frozen=True)
class SenderIdentity:
    """Resolved sender identity used for outgoing email headers."""

    from_email: str
    from_name: str


def normalize_email_address(value: str) -> str:
    """Normalize and validate an email address used in a mail header."""
    if not isinstance(value, str):
        raise ValueError("Alamat email tidak valid")
    normalized = value.strip().lower()
    if not _EMAIL_PATTERN.fullmatch(normalized):
        raise ValueError("Alamat email tidak valid")
    return normalized


def normalize_sender_name(value: str) -> str:
    """Normalize a display name and reject control characters/newlines."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Nama pengirim wajib diisi")
    normalized = value.strip()
    if _CONTROL_CHARS_PATTERN.search(normalized):
        raise ValueError("Nama pengirim mengandung karakter kontrol")
    return normalized


def sanitize_header_value(value: str) -> str:
    """Reject CR/LF and other control characters in a raw header value."""
    if not isinstance(value, str):
        raise ValueError("Nilai header email tidak valid")
    normalized = value.strip()
    if not normalized or _CONTROL_CHARS_PATTERN.search(normalized):
        raise ValueError("Nilai header email tidak valid")
    return normalized


def format_mailbox(name: str, address: str) -> str:
    """Build a safe ``Name <address>`` header value.

    ``formataddr`` quotes display names that contain special characters, while
    the validators reject header-injection characters.
    """
    return formataddr((normalize_sender_name(name), normalize_email_address(address)))


async def resolve_sender_identity(db: AsyncSession) -> SenderIdentity:
    """Return the owner-managed sender, falling back to environment values.

    The database value wins when present. Empty or missing database fields fall
    back individually so a partially populated row cannot blank the sender.
    """
    row = await db.get(EmailSenderSettings, 1)
    db_email = (row.from_email or "").strip() if row else ""
    db_name = (row.from_name or "").strip() if row else ""
    return SenderIdentity(
        from_email=db_email or settings.smtp_from_email.strip(),
        from_name=db_name or settings.smtp_from_name.strip(),
    )
