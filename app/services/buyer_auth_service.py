import secrets
import uuid
import string
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.repositories import buyer_repo, otp_repo, user_repo
from app.models.buyer import Buyer
from app.core.security import hash_password, verify_password, create_access_token
from app.models.otp_code import OTPCode
from app.core.config import settings
from app.utils.phone import normalize_phone
from app.utils.cloudinary_helper import upload_image_to_cloudinary

# ── GENERAL OTP HELPERS ───────────────────────────────────────────────────────

async def send_otp(db: AsyncSession, target: str, channel: str, purpose: str) -> dict:
    """Generate and store OTP in database, returning OTP ID and expiration"""
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

    return {
        "otp_id": otp_id,
        "expires_in": 300
    }


async def verify_otp(db: AsyncSession, otp_id: str, code: str) -> dict:
    """Verify OTP code from database and issue a single-use DB verify_token"""
    otp = await otp_repo.get_otp_by_id(db, otp_id)
    if not otp:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Kode OTP tidak valid atau tidak ditemukan."
        )

    if otp.is_used:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Kode OTP sudah digunakan."
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Kode OTP salah."
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

async def validate_and_consume_db_verify_token(db: AsyncSession, verify_token: str, phone_number: str):
    stmt = select(OTPCode).where(OTPCode.verify_token == verify_token)
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
        
    # Mark as used
    otp.is_used = True
    await db.commit()
    await db.refresh(otp)




# ── WA DEEP LINK OTP ──────────────────────────────────────────────────────────

def generate_nonce(length: int = 6) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


async def start_wa_verification(db: AsyncSession, phone_number: str) -> dict:
    phone_number = normalize_phone(phone_number)
    
    is_mock = (settings.WA_VERIFICATION_MODE == "mock")
    
    if not is_mock and not settings.chatbot_wa_number:
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
    
    deeplink = f"https://wa.me/{settings.chatbot_wa_number}?text=VERIFIKASI%20{nonce}"
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
    # Validate and consume verify token from DB
    await validate_and_consume_db_verify_token(db, verify_token, phone)
        
    # Check duplicate buyer
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
        
    # Create buyer (set is_verified to True because OTP was verified)
    pwd_hash = hash_password(password)
    buyer = await buyer_repo.create_buyer(
        db=db,
        name=name,
        email=email,
        phone=phone,
        password_hash=pwd_hash,
        is_verified=True
    )
    
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
    buyer = await buyer_repo.get_buyer_by_email(db, email)
    if not buyer:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password"
        )
        
    if not verify_password(password, buyer.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password"
        )
        
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
    buyer = await buyer_repo.get_buyer_by_phone(db, phone)
    if not buyer or not buyer.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid phone number or password"
        )
        
    if not verify_password(password, buyer.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid phone number or password"
        )
        
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
    # Validate and consume verify token from DB
    await validate_and_consume_db_verify_token(db, verify_token, phone)
        
    buyer = await buyer_repo.get_buyer_by_phone(db, phone)
    if not buyer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No buyer account found with this phone number. Please register first."
        )
        
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



async def reset_buyer_password(db: AsyncSession, verify_token: str, new_password: str) -> dict:
    """Reset buyer password via verification token"""
    stmt = select(OTPCode).where(OTPCode.verify_token == verify_token)
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

    otp_expires = otp.expires_at
    if otp_expires.tzinfo is None:
        otp_expires = otp_expires.replace(tzinfo=timezone.utc)
    if otp_expires < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token verifikasi telah kedaluwarsa."
        )

    target = otp.phone_number or otp.target
    otp.is_used = True
    await db.commit()

    # Target can be email or phone
    buyer = await buyer_repo.get_buyer_by_email(db, target)
    if not buyer:
        buyer = await buyer_repo.get_buyer_by_phone(db, target)

    if not buyer:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Buyer account not found"
        )

    # Hash and update password
    pwd_hash = hash_password(new_password)
    await buyer_repo.update_buyer_password(db, buyer, pwd_hash)

    return {"message": "Password reset successful"}


# ── SELLER AUTH ──────────────────────────────────────────────────────────────

async def request_seller_forgot_password(db: AsyncSession, email: str) -> dict:
    """Request password reset for a seller/internal user (matches username to email)"""
    user = await user_repo.get_user_by_username(db, email)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Seller account not found with this email"
        )

    # Send mock OTP
    return await send_otp(db, email, "email", "reset_password")


async def verify_seller_forgot_password(db: AsyncSession, otp_id: str, code: str) -> dict:
    """Verify OTP for seller password reset and return verify_token"""
    return await verify_otp(db, otp_id, code)


async def reset_seller_password(db: AsyncSession, verify_token: str, new_password: str) -> dict:
    """Reset seller/internal user password via verification token"""
    stmt = select(OTPCode).where(OTPCode.verify_token == verify_token)
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

    otp_expires = otp.expires_at
    if otp_expires.tzinfo is None:
        otp_expires = otp_expires.replace(tzinfo=timezone.utc)
    if otp_expires < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Token verifikasi telah kedaluwarsa."
        )

    target = otp.target
    otp.is_used = True
    await db.commit()

    user = await user_repo.get_user_by_username(db, target)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Seller account not found"
        )

    # Update user password in DB
    user.password_hash = hash_password(new_password)
    await db.commit()
    await db.refresh(user)

    return {"message": "Password reset successful"}

