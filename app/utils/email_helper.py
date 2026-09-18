import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from app.core.config import settings

logger = logging.getLogger(__name__)


async def send_otp_email(to_email: str, otp_code: str) -> bool:
    """
    Send OTP code to buyer's email for password reset.

    If SMTP is not configured (smtp_host is empty), logs the OTP to console
    (mock mode for development/testing) and returns True.
    """
    subject = f"Kode OTP Reset Password — {settings.smtp_from_name}"
    html_body = f"""\
    <html>
    <body style="font-family: Arial, sans-serif; padding: 20px;">
        <h2 style="color: #d4763b;">{settings.smtp_from_name}</h2>
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
            — Tim {settings.smtp_from_name}
        </p>
    </body>
    </html>
    """

    # ── Mock mode: SMTP not configured ────────────────────────────────────────
    if not settings.smtp_host:
        logger.warning(
            f"[EMAIL MOCK] SMTP not configured. OTP for {to_email}: {otp_code}"
        )
        return True

    # ── Real SMTP sending ─────────────────────────────────────────────────────
    try:
        import aiosmtplib

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{settings.smtp_from_name} <{settings.smtp_from_email}>"
        msg["To"] = to_email
        msg.attach(MIMEText(html_body, "html"))

        await aiosmtplib.send(
            msg,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            start_tls=settings.smtp_use_tls,
        )
        logger.info(f"[EMAIL] OTP sent successfully to {to_email}")
        return True

    except Exception as e:
        logger.error(f"[EMAIL] Failed to send OTP to {to_email}: {e}")
        return False
