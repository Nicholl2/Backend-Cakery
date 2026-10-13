import secrets
import uuid
import string
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.repositories import buyer_repo, otp_repo, user_repo
from app.models.buyer import Buyer
from app.core.security import hash_password, verify_password, create_access_token
from app.core.cache import app_cache
from app.models.otp_code import OTPCode
from app.core.config import settings
from app.utils.phone import normalize_phone
from app.utils.cloudinary_helper import upload_image_to_cloudinary
from app.services.chatbot_notify import fetch_whatsapp_number
from app.utils.email_helper import send_otp_email  # top-level for testability

# ── BUYER LOGIN ATTEMPT TRACKING ─────────────────────────────────────────────
_MAX_BUYER_LOGIN_ATTEMPTS = 5
_BUYER_LOCKOUT_SECONDS = 15 * 60  # 15 menit


def _buyer_login_key(identifier: str) -> str:
    return f"login_attempts:buyer:{identifier.lower().strip()}"


def _check_buyer_lockout(identifier: str) -> None:
    """Raise 429 if the buyer identifier has exceeded max login attempts."""
    key = _buyer_login_key(identifier)
    attempts = app_cache.get(key)
    if attempts is not None and attempts >= _MAX_BUYER_LOGIN_ATTEMPTS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "Terlalu banyak percobaan login gagal. "
                "Akun dikunci sementara selama 15 menit. Silakan coba lagi nanti."
            ),
        )


def _record_buyer_failed_login(identifier: str) -> None:
    """Increment failed buyer login attempt counter."""
    key = _buyer_login_key(identifier)
    current = app_cache.get(key) or 0
    app_cache.set(key, current + 1, ttl=_BUYER_LOCKOUT_SECONDS)


def _clear_buyer_login_attempts(identifier: str) -> None:
    """Reset buyer login attempt counter on successful login."""
    key = _buyer_login_key(identifier)
    app_cache.invalidate(key)

# ── GENERAL OTP HELPERS ───────────────────────────────────────────────────────

async def send_otp(db: AsyncSession, target: str, channel: str, purpose: str) -> dict:
    """
    Generate, store, and deliver an OTP.

    Stores OTP in DB first, then sends it via the configured channel.
    Raises HTTPException 503 if the email provider fails so callers can
    surface the error instead of silently pretending the OTP was sent.
    """
    code = "".join(secrets.choice(string.digits) for _ in range(6))
    otp_id = str(uuid.uuid4())
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    code_hash = hash_password(code)

    await otp_repo.create_otp(
        db=db,
        otp_id=otp_id,
        target=target,
        channel=channel,
        purpose=purpose,
        code_hash=code_hash,
        expires_at=expires_at
    )

    if channel == "email":
        sent = await send_otp_email(target, code)
        if not sent:
            # OTP saved; caller may retry — do not expose the code
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Gagal mengirim kode OTP ke email. Coba lagi dalam beberapa saat."
            )

    return {
        "otp_id": otp_id,
        "expires_in": 300
    }


_MAX_OTP_VERIFY_ATTEMPTS = 5


async def verify_otp(db: AsyncSession, otp_id: str, code: str) -> dict:
    """Verify OTP code from database and issue a single-use DB verify_token.
    
    Uses WITH FOR UPDATE to prevent concurrent requests from both passing the
    is_used check and both receiving a verify_token for the same OTP.

    NOTE: WITH FOR UPDATE works on PostgreSQL. SQLite (used in tests) silently
    degrades — concurrent safety is only guaranteed on PostgreSQL.
    """
    stmt = (
        select(OTPCode)
        .where(OTPCode.id == otp_id)
        .with_for_update()
    )
    res = await db.execute(stmt)
    otp = res.scalars().first()

    if not otp:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Kode OTP tidak valid atau tidak ditemukan."
        )

    if otp.is_used:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Kode OTP sudah digunakan atau telah kedaluwarsa."
        )

    if (otp.attempt_count or 0) >= _MAX_OTP_VERIFY_ATTEMPTS:
        otp.is_used = True
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Batas percobaan verifikasi OTP tercapai (maksimal 5 kali). Silakan minta kode baru."
        )

    otp_expires = otp.expires_at
    if otp_expires.tzinfo is None:
        otp_expires = otp_expires.replace(tzinfo=timezone.utc)
    if otp_expires < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Kode OTP telah kedaluwarsa."
        )

    if not verify_password(code, otp.code_hash):
        otp.attempt_count = (otp.attempt_count or 0) + 1
        await db.commit()
        if otp.attempt_count >= _MAX_OTP_VERIFY_ATTEMPTS:
            otp.is_used = True
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Batas percobaan verifikasi OTP tercapai (maksimal 5 kali). Silakan minta kode baru."
            )
        remaining = _MAX_OTP_VERIFY_ATTEMPTS - otp.attempt_count
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Kode OTP salah. Sisa percobaan: {remaining}."
        )

    verify_token = str(uuid.uuid4())
    otp.is_verified = True
    otp.verify_token = verify_token
    await db.commit()
    await db.refresh(otp)

    return {
        "verify_token": verify_token,
        "target": otp.target
    }



# ── DB VERIFICATION TOKENS ────────────────────────────────────────────────────

async def validate_and_consume_db_verify_token(db: AsyncSession, verify_token: str, phone_number: str, purpose: str = "login"):
    """
    Validate and atomically consume a DB-backed verify_token.

    Security properties:
    - WITH FOR UPDATE prevents concurrent requests from both passing the is_used check.
    - is_verified is checked to ensure only fully confirmed WA tokens are accepted.
    - Token is bound to the phone_number that initiated the OTP flow.
    - Token must not be expired.

    NOTE: WITH FOR UPDATE works on PostgreSQL. SQLite (used in tests) does not
    support it but silently degrades — concurrent safety in tests is not guaranteed.
    """
    from sqlalchemy.dialects import postgresql  # noqa: F401 — for awareness

    stmt = (
        select(OTPCode)
        .where(OTPCode.verify_token == verify_token, OTPCode.purpose == purpose)
        .with_for_update()
    )
    res = await db.execute(stmt)
    otp = res.scalars().first()

    if not otp:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token verifikasi tidak valid atau tidak ditemukan."
        )

    if otp.is_used:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token verifikasi sudah digunakan."
        )

    if not otp.is_verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token verifikasi belum dikonfirmasi."
        )

    try:
        cleaned_phone = normalize_phone(phone_number)
        cleaned_otp_phone = normalize_phone(otp.phone_number) if otp.phone_number else ""
    except HTTPException:
        cleaned_phone = phone_number.strip()
        cleaned_otp_phone = (otp.phone_number or "").strip()

    if cleaned_otp_phone != cleaned_phone:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token verifikasi tidak cocok dengan nomor telepon tujuan."
        )

    otp_expires = otp.expires_at
    if otp_expires.tzinfo is None:
        otp_expires = otp_expires.replace(tzinfo=timezone.utc)

    if otp_expires < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token verifikasi telah kedaluwarsa."
        )

    # The caller commits consumption together with its business operation.
    otp.is_used = True
    return otp






# ── WA DEEP LINK OTP ──────────────────────────────────────────────────────────

def generate_nonce(length: int = 6) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


async def start_wa_verification(db: AsyncSession, phone_number: str) -> dict:
    phone_number = normalize_phone(phone_number)
    
    # `login` is shared by OTP login and buyer registration to preserve the
    # existing WA start API contract.
    is_mock = (settings.WA_VERIFICATION_MODE == "mock")
    
    wa_number = ""
    if not is_mock:
        wa_number = await fetch_whatsapp_number()
        if not wa_number:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Layanan verifikasi WA belum dikonfigurasi. Hubungi administrator."
            )

    # Generate unique 6-char alphanumeric nonce
    nonce = generate_nonce(6)
    for _ in range(5):
        stmt = select(OTPCode).where(OTPCode.nonce == nonce)
        res = await db.execute(stmt)
        if not res.scalars().first():
            break
        nonce = generate_nonce(6)
        
    otp_id = str(uuid.uuid4())
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    
    if is_mock:
        verify_token = str(uuid.uuid4())
        otp = OTPCode(
            id=otp_id,
            target=phone_number,
            phone_number=phone_number,
            channel="whatsapp",
            purpose="login",
            code_hash=hash_password(nonce),
            expires_at=expires_at,
            is_used=False,
            nonce=nonce,
            is_verified=True,
            verify_token=verify_token,
            attempt_count=0
        )
        db.add(otp)
        await db.commit()
        
        return {
            "nonce": nonce,
            "deeplink": "",
            "expires_in": 600,
            "verify_token": verify_token,
            "mock_mode": True
        }
    
    otp = OTPCode(
        id=otp_id,
        target=phone_number,
        phone_number=phone_number,
        channel="whatsapp",
        purpose="login",
        code_hash=hash_password(nonce),
        expires_at=expires_at,
        is_used=False,
        nonce=nonce,
        is_verified=False,
        attempt_count=0
    )
    db.add(otp)
    await db.commit()
    
    deeplink = f"https://wa.me/{wa_number}?text=VERIFIKASI%20{nonce}"
    return {
        "nonce": nonce,
        "deeplink": deeplink,
        "expires_in": 600,
        "verify_token": None,
        "mock_mode": False
    }


_MAX_CONFIRM_ATTEMPTS = 3


async def confirm_wa_verification(db: AsyncSession, nonce: str, sender_phone: str) -> dict:
    """
    Called exclusively by the chatbot with X-Service-Key / X-Internal-Key.
    Validates the nonce and that sender_phone matches the originating phone.
    Does NOT overwrite otp.phone_number; only compares normalized values.
    """
    normalized_sender = normalize_phone(sender_phone)
    
    stmt = select(OTPCode).where(OTPCode.nonce == nonce)
    res = await db.execute(stmt)
    otp = res.scalars().first()
    
    # ── 404: not found, already used, or expired ─────────────────────────────
    if not otp:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Nonce verifikasi tidak ditemukan."
        )
    if otp.is_used or otp.is_verified:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Nonce verifikasi sudah digunakan atau telah diverifikasi."
        )
    otp_expires = otp.expires_at
    if otp_expires.tzinfo is None:
        otp_expires = otp_expires.replace(tzinfo=timezone.utc)
    if otp_expires < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Nonce verifikasi telah kedaluwarsa."
        )
    
    # ── Phone mismatch: increment attempt counter ─────────────────────────────
    normalized_original = normalize_phone(otp.phone_number) if otp.phone_number else ""
    if normalized_sender != normalized_original:
        otp.attempt_count = (otp.attempt_count or 0) + 1
        await db.commit()
        if otp.attempt_count >= _MAX_CONFIRM_ATTEMPTS:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Batas percobaan verifikasi tercapai (maksimal 3 kali). Silakan minta kode verifikasi baru."
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Nomor pengirim tidak cocok dengan nomor yang mendaftarkan nonce ini. "
                f"Sisa percobaan: {_MAX_CONFIRM_ATTEMPTS - otp.attempt_count}."
            )
        )
    
    # ── Success: mark verified ───────────────────────────────────────────────
    verify_token = str(uuid.uuid4())
    otp.is_verified = True
    otp.verify_token = verify_token
    await db.commit()
    await db.refresh(otp)
    
    return {
        "status": "ok",
        "message": "Phone verified successfully"
    }


async def check_wa_verification_status(db: AsyncSession, nonce: str) -> dict:
    stmt = select(OTPCode).where(OTPCode.nonce == nonce)
    res = await db.execute(stmt)
    otp = res.scalars().first()
    
    if not otp:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Nonce verifikasi tidak ditemukan."
        )
        
    is_mock = (settings.WA_VERIFICATION_MODE == "mock")
    if is_mock:
        if not otp.is_verified:
            otp.is_verified = True
            if not otp.verify_token:
                otp.verify_token = str(uuid.uuid4())
            await db.commit()
            await db.refresh(otp)
        return {
            "status": "verified",
            "verify_token": otp.verify_token
        }
        
    if otp.is_verified:
        return {
            "status": "verified",
            "verify_token": otp.verify_token
        }
    else:
        return {
            "status": "pending"
        }




# ── BUYER AUTH ───────────────────────────────────────────────────────────────

async def register_buyer(
    db: AsyncSession,
    name: str,
    email: str,
    phone: str,
    password: str,
    verify_token: str
) -> dict:
    """Register a new buyer if verification token is valid"""
    phone = normalize_phone(phone)

    from app.core.security import validate_password_strength
    try:
        validate_password_strength(password)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    # ── Validate duplicates BEFORE consuming the token ──────────────────────
    # This prevents the token from being burned on validation failures.
    existing_email = await buyer_repo.get_buyer_by_email(db, email)
    if existing_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email is already registered"
        )

    existing_phone = await buyer_repo.get_buyer_by_phone(db, phone)
    if existing_phone:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Phone number is already registered"
        )

    # ── Consume token only after all pre-checks pass ─────────────────────────
    await validate_and_consume_db_verify_token(db, verify_token, phone, purpose="login")

    # Create buyer (set is_verified to True because OTP was verified)
    pwd_hash = hash_password(password)
    buyer = Buyer(name=name, email=email, phone=phone, password_hash=pwd_hash, is_verified=True)
    db.add(buyer)
    try:
        await db.commit()
    except IntegrityError:
        # Roll back token consumption with the failed insert. A competing
        # registration may have won the unique email/phone constraint.
        await db.rollback()
        if await buyer_repo.get_buyer_by_email(db, email):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email is already registered")
        if await buyer_repo.get_buyer_by_phone(db, phone):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Phone number is already registered")
        raise
    await db.refresh(buyer)

    # Generate JWT
    access_token = create_access_token(
        user_id=buyer.id,
        role_level=0,
        username=buyer.email,
        role="buyer"
    )

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user_id": buyer.id,
        "role": "buyer",
        "name": buyer.name,
        "email": buyer.email,
        "phone": buyer.phone,
        "avatar_url": buyer.avatar_url,
    }


async def login_buyer_password(db: AsyncSession, email: str, password: str) -> dict:
    """Login buyer via email and password"""
    _check_buyer_lockout(email)

    buyer = await buyer_repo.get_buyer_by_email(db, email)
    if not buyer:
        _record_buyer_failed_login(email)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password"
        )

    if not verify_password(password, buyer.password_hash):
        _record_buyer_failed_login(email)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password"
        )

    # Check is_active AFTER password verification to avoid active-user enumeration
    if not buyer.is_active:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Akun buyer tidak aktif.",
        )

    _clear_buyer_login_attempts(email)

    # Generate JWT
    access_token = create_access_token(
        user_id=buyer.id,
        role_level=0,
        username=buyer.email,
        role="buyer"
    )

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user_id": buyer.id,
        "role": "buyer",
        "name": buyer.name,
        "email": buyer.email,
        "phone": buyer.phone,
        "avatar_url": buyer.avatar_url,
    }


async def login_buyer_phone(db: AsyncSession, phone: str, password: str) -> dict:
    """Login buyer via phone number and password"""
    phone = normalize_phone(phone)
    _check_buyer_lockout(phone)

    buyer = await buyer_repo.get_buyer_by_phone(db, phone)
    if not buyer or not buyer.is_active:
        _record_buyer_failed_login(phone)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid phone number or password"
        )
        
    if not verify_password(password, buyer.password_hash):
        _record_buyer_failed_login(phone)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid phone number or password"
        )

    _clear_buyer_login_attempts(phone)
        
    # Generate JWT
    access_token = create_access_token(
        user_id=buyer.id,
        role_level=0,
        username=buyer.email,
        role="buyer"
    )
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user_id": buyer.id,
        "role": "buyer",
        "name": buyer.name,
        "email": buyer.email,
        "phone": buyer.phone,
        "avatar_url": buyer.avatar_url,
    }


async def login_buyer_otp(db: AsyncSession, phone: str, verify_token: str) -> dict:
    """Login buyer via OTP verification token"""
    phone = normalize_phone(phone)
    buyer = await buyer_repo.get_buyer_by_phone(db, phone)
    if not buyer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No buyer account found with this phone number. Please register first."
        )

    if not buyer.is_active:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Akun buyer tidak aktif.",
        )

    # Consume only after the account is known to be eligible for login. The
    # caller commits this with successful authentication below.
    await validate_and_consume_db_verify_token(db, verify_token, phone)
    await db.commit()

    # Generate JWT
    access_token = create_access_token(
        user_id=buyer.id,
        role_level=0,
        username=buyer.email,
        role="buyer"
    )

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user_id": buyer.id,
        "role": "buyer",
        "name": buyer.name,
        "email": buyer.email,
        "phone": buyer.phone,
        "avatar_url": buyer.avatar_url,
    }


async def upload_buyer_avatar(db: AsyncSession, buyer: Buyer, file: UploadFile) -> Buyer:
    """
    Upload and update buyer avatar image to Cloudinary (folder: toti-cakery/avatars).
    """
    secure_url = await upload_image_to_cloudinary(file, folder="toti-cakery/avatars")
    updated_buyer = await buyer_repo.update_avatar_url(db, buyer, secure_url)
    return updated_buyer


async def change_buyer_password(
    db: AsyncSession, buyer: Buyer, current_password: str, new_password: str
) -> dict:
    """Change password for authenticated buyer — requires current password verification."""
    if not verify_password(current_password, buyer.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password saat ini tidak sesuai."
        )

    from app.core.security import validate_password_strength
    try:
        validate_password_strength(new_password)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    new_hash = hash_password(new_password)
    await buyer_repo.update_buyer_password(db, buyer, new_hash)
    return {"message": "Password berhasil diperbarui."}


async def change_buyer_phone(
    db: AsyncSession, buyer: Buyer, current_password: str, new_phone: str
) -> Buyer:
    """Change phone number for authenticated buyer — requires current password verification."""
    if not verify_password(current_password, buyer.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password saat ini tidak sesuai."
        )

    normalized = normalize_phone(new_phone)

    existing = await buyer_repo.get_buyer_by_phone(db, normalized)
    if existing and existing.id != buyer.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Nomor WhatsApp sudah terdaftar."
        )

    updated_buyer = await buyer_repo.update_buyer_phone(db, buyer, normalized)
    return updated_buyer


async def reset_buyer_password(db: AsyncSession, verify_token: str, new_password: str) -> dict:
    """Reset buyer password via verification token"""
    from app.core.security import validate_password_strength

    try:
        validate_password_strength(new_password)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    stmt = select(OTPCode).where(OTPCode.verify_token == verify_token, OTPCode.purpose.in_(("buyer_reset_password", "reset_password"))).with_for_update()
    res = await db.execute(stmt)
    otp = res.scalars().first()

    if not otp:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token verifikasi tidak valid atau tidak ditemukan."
        )

    if otp.is_used or not otp.is_verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token verifikasi sudah digunakan."
        )

    otp_expires = otp.expires_at
    if otp_expires.tzinfo is None:
        otp_expires = otp_expires.replace(tzinfo=timezone.utc)
    if otp_expires < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token verifikasi telah kedaluwarsa."
        )

    target = otp.phone_number or otp.target

    # ── Look up buyer BEFORE consuming the token ──────────────────────────────
    # This ensures the token is not burned if the buyer doesn't exist.
    buyer = await buyer_repo.get_buyer_by_email(db, target)
    if not buyer:
        buyer = await buyer_repo.get_buyer_by_phone(db, target)

    if not buyer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Buyer account not found"
        )

    # ── Atomically consume token + update password in a single commit ─────────
    otp.is_used = True
    await db.execute(
        OTPCode.__table__.update().where(
            OTPCode.target == target,
            OTPCode.purpose.in_(("buyer_reset_password", "reset_password")),
            OTPCode.id != otp.id,
            OTPCode.is_used == False,  # noqa: E712
        ).values(is_used=True)
    )
    buyer.password_hash = hash_password(new_password)
    await db.commit()

    return {"message": "Password reset successful"}


# ── BUYER FORGOT/RESET PASSWORD VIA EMAIL ────────────────────────────────────

async def request_buyer_forgot_password(db: AsyncSession, email: str) -> dict:
    """
    Request password reset OTP via email for buyer.
    Always returns HTTP 200 to prevent email enumeration.
    If the email provider fails, the failure is logged but the response is
    still 200 — the OTP remains in DB and the user can retry.
    """
    import logging
    _logger = logging.getLogger(__name__)
    buyer = await buyer_repo.get_buyer_by_email(db, email)

    if buyer:
        # Generate 6-digit OTP
        code = "".join(secrets.choice(string.digits) for _ in range(6))
        otp_id = str(uuid.uuid4())
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
        code_hash = hash_password(code)

        otp = OTPCode(
            id=otp_id,
            target=email,
            channel="email",
            purpose="buyer_reset_password",
            code_hash=code_hash,
            expires_at=expires_at,
            is_used=False,
            attempt_count=0,
        )
        db.add(otp)
        await db.commit()

        sent = await send_otp_email(email, code)
        if not sent:
            # OTP is persisted; log failure without exposing code/email details
            _logger.warning(
                "[AUTH] Failed to deliver buyer password-reset OTP via email. "
                "OTP persisted in DB — user can retry."
            )

    # Always return success — prevent email enumeration
    return {"message": "Kode OTP reset password telah dikirim ke email Anda."}


async def reset_buyer_password_email(
    db: AsyncSession, email: str, otp_code: str, new_password: str
) -> dict:
    """Reset buyer password via email + OTP verification."""
    from app.core.security import validate_password_strength

    validate_password_strength(new_password)

    # Find the latest unused OTP for this email & purpose
    stmt = (
        select(OTPCode)
        .where(
            OTPCode.target == email,
            OTPCode.purpose == "buyer_reset_password",
            OTPCode.is_used == False,  # noqa: E712
        )
        .order_by(OTPCode.created_at.desc())
        .with_for_update()
    )
    res = await db.execute(stmt)
    otp = res.scalars().first()

    if not otp:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Kode OTP tidak valid atau tidak ditemukan."
        )

    # Check max attempts — lock OTP if exceeded
    if (otp.attempt_count or 0) >= _MAX_OTP_VERIFY_ATTEMPTS:
        otp.is_used = True
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Batas percobaan verifikasi OTP tercapai (maksimal 5 kali). Silakan minta kode baru."
        )

    # Check expiry
    otp_expires = otp.expires_at
    if otp_expires.tzinfo is None:
        otp_expires = otp_expires.replace(tzinfo=timezone.utc)
    if otp_expires < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Kode OTP telah kedaluwarsa."
        )

    # Verify OTP code against hash — increment attempts on failure
    if not verify_password(otp_code, otp.code_hash):
        otp.attempt_count = (otp.attempt_count or 0) + 1
        await db.commit()
        if otp.attempt_count >= _MAX_OTP_VERIFY_ATTEMPTS:
            otp.is_used = True
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Batas percobaan verifikasi OTP tercapai (maksimal 5 kali). Silakan minta kode baru."
            )
        remaining = _MAX_OTP_VERIFY_ATTEMPTS - otp.attempt_count
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Kode OTP salah. Sisa percobaan: {remaining}."
        )

    # ── Look up buyer BEFORE consuming the OTP ───────────────────────────────
    # This ensures the OTP is not burned if the buyer account doesn't exist.
    buyer = await buyer_repo.get_buyer_by_email(db, email)
    if not buyer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Akun Buyer tidak ditemukan."
        )

    # ── Atomically consume OTP + update password in a single commit ───────────
    otp.is_used = True
    await db.execute(
        OTPCode.__table__.update().where(
            OTPCode.target == email,
            OTPCode.purpose == "buyer_reset_password",
            OTPCode.id != otp.id,
            OTPCode.is_used == False,  # noqa: E712
        ).values(is_used=True)
    )
    buyer.password_hash = hash_password(new_password)
    await db.commit()

    return {"message": "Password berhasil di-reset. Silakan login kembali."}


# ── SELLER AUTH ──────────────────────────────────────────────────────────────

async def request_seller_forgot_password(db: AsyncSession, email: str) -> dict:
    """Request password reset for a seller/internal user (prevents email enumeration)"""
    user = await user_repo.get_user_by_username(db, email)
    if not user:
        user = await user_repo.get_user_by_email(db, email)

    if user:
        target_email = user.email or user.username
        return await send_otp(db, target_email, "email", "reset_password")

    # Anti-enumeration response: return fake otp_id to protect user privacy
    return {
        "otp_id": str(uuid.uuid4()),
        "expires_in": 300
    }



async def verify_seller_forgot_password(db: AsyncSession, otp_id: str, code: str) -> dict:
    """Verify OTP for seller password reset and return verify_token"""
    return await verify_otp(db, otp_id, code)


async def reset_seller_password(db: AsyncSession, verify_token: str, new_password: str) -> dict:
    """Reset seller/internal user password via verification token"""
    from app.core.security import validate_password_strength

    try:
        validate_password_strength(new_password)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    stmt = select(OTPCode).where(OTPCode.verify_token == verify_token, OTPCode.purpose == "reset_password").with_for_update()
    res = await db.execute(stmt)
    otp = res.scalars().first()

    if not otp:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token verifikasi tidak valid atau tidak ditemukan."
        )

    if otp.is_used or not otp.is_verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token verifikasi sudah digunakan."
        )

    otp_expires = otp.expires_at
    if otp_expires.tzinfo is None:
        otp_expires = otp_expires.replace(tzinfo=timezone.utc)
    if otp_expires < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token verifikasi telah kedaluwarsa."
        )

    target = otp.target

    # ── Look up user BEFORE consuming the token ───────────────────────────────
    # target is the email address set by request_seller_forgot_password.
    user = await user_repo.get_user_by_email(db, target)
    if not user:
        user = await user_repo.get_user_by_username(db, target)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Seller account not found"
        )

    # ── Atomically consume token + update password in a single commit ─────────
    otp.is_used = True
    await db.execute(
        OTPCode.__table__.update().where(
            OTPCode.target == target,
            OTPCode.purpose == "reset_password",
            OTPCode.id != otp.id,
            OTPCode.is_used == False,  # noqa: E712
        ).values(is_used=True)
    )
    user.password_hash = hash_password(new_password)
    await db.commit()
    await db.refresh(user)

    return {"message": "Password reset successful"}

