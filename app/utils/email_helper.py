import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import TYPE_CHECKING

from app.core.config import settings
from app.utils.email_identity import (
    SenderIdentity,
    format_mailbox,
    resolve_sender_identity,
    sanitize_header_value,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def send_otp_email(
    to_email: str,
    otp_code: str,
    db: "AsyncSession | None" = None,
) -> bool:
    """
    Send OTP code to buyer's email for password reset.

    The sender identity follows the same owner-managed setting as transactional
    notifications, with the SMTP environment values as fallback.

    If SMTP is not configured, report delivery failure. A log entry is not
    delivery and must never be treated as a successful production email.
    """
    # ── Mock mode: SMTP not configured ────────────────────────────────────────
    if not settings.smtp_host:
        logger.warning("[EMAIL MOCK] SMTP is not configured; OTP email was not delivered")
        return False

    # ── Real SMTP sending ─────────────────────────────────────────────────────
    try:
        import aiosmtplib

        if db is not None:
            sender = await resolve_sender_identity(db)
        else:
            sender = SenderIdentity(
                from_email=settings.smtp_from_email,
                from_name=settings.smtp_from_name,
            )

        subject = f"Kode OTP Reset Password — {sender.from_name}"
        html_body = f"""\
        <html>
        <body style="font-family: Arial, sans-serif; padding: 20px;">
            <h2 style="color: #d4763b;">{sender.from_name}</h2>
            <p>Halo,</p>
            <p>Anda telah meminta reset password untuk akun Anda.
               Gunakan kode OTP berikut untuk melanjutkan:</p>
            <div style="background: #f5f5f5; padding: 16px 24px; border-radius: 8px;
                        display: inline-block; margin: 16px 0;">
                <span style="font-size: 28px; font-weight: bold; letter-spacing: 6px;
                             color: #333;">{otp_code}</span>
            </div>
            <p>Kode ini berlaku selama <strong>10 menit</strong>.</p>
            <p>Jika Anda tidak meminta reset password, abaikan email ini.</p>
            <br>
            <p style="color: #888; font-size: 12px;">
                — Tim {sender.from_name}
            </p>
        </body>
        </html>
        """

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = format_mailbox(sender.from_name, sender.from_email)
        msg["To"] = sanitize_header_value(to_email)
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        await aiosmtplib.send(
            msg,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            start_tls=settings.smtp_use_tls,
            timeout=15,
        )
        logger.info("[EMAIL] OTP email delivered successfully")
        return True

    except Exception:
        logger.error("[EMAIL] OTP delivery failed; recipient and message details suppressed")
        return False
