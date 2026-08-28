import secrets
import uuid
import string
from datetime import datetime, timedelta, timezone
from typing import Dict, Any

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.repositories import buyer_repo, otp_repo, user_repo
from app.core.security import hash_password, verify_password, create_access_token
from app.models.otp_code import OTPCode
from app.core.config import settings

# In-memory store for single-use verified tokens (verify_token)
# Format: { token_str: { "target": str, "purpose": str, "expires_at": datetime } }
VERIFIED_TOKENS: Dict[str, Dict[str, Any]] = {}


def clean_expired_tokens():
    """Remove expired verification tokens from memory"""
    now = datetime.now(timezone.utc)
    expired = [k for k, v in VERIFIED_TOKENS.items() if v["expires_at"] < now]
    for k in expired:
        VERIFIED_TOKENS.pop(k, None)


def generate_verify_token(target: str, purpose: str) -> str:
    """Generate a secure, single-use verification token valid for 10 minutes"""
    clean_expired_tokens()
    token = secrets.token_hex(32)
    VERIFIED_TOKENS[token] = {
        "target": target,
        "purpose": purpose,
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=10)
    }
    return token


def consume_verify_token(token: str, expected_purpose: str) -> str:
    """Validate and consume a single-use verification token, returning its target"""
    clean_expired_tokens()
    if token not in VERIFIED_TOKENS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Verification token is invalid or has already been used"
        )
    
    token_data = VERIFIED_TOKENS[token]
    if token_data["expires_at"] < datetime.now(timezone.utc):
        VERIFIED_TOKENS.pop(token, None)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Verification token has expired"
        )
        
    if token_data["purpose"] != expected_purpose:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Verification token purpose mismatch. Expected: {expected_purpose}"
        )
        
    # Consume (remove from memory) to ensure single-use
    VERIFIED_TOKENS.pop(token)
    return token_data["target"]


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
        
    cleaned_phone = phone_number.strip().replace(" ", "").replace("+", "")
    cleaned_otp_phone = otp.phone_number.strip().replace(" ", "").replace("+", "") if otp.phone_number else ""
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


def generate_nonce(length: int = 6) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


async def start_wa_verification(db: AsyncSession, phone_number: str) -> dict:
    phone_number = phone_number.strip().replace(" ", "").replace("+", "")
    
    # Generate unique 6-char alphanumeric nonce
    nonce = generate_nonce(6)
    for _ in range(5):
        stmt = select(OTPCode).where(OTPCode.nonce == nonce)
        res = await db.execute(stmt)
        if not res.scalars().first():
            break
        nonce = generate_nonce(6)
        
    otp_id = str(uuid.uuid4())
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=10) # 10 minutes expiry
    
    # Store in DB
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
        is_verified=False
    )
    db.add(otp)
    await db.commit()
    
    deeplink = f"https://wa.me/{settings.chatbot_wa_number}?text=VERIFIKASI%20{nonce}"
    return {
        "nonce": nonce,
        "deeplink": deeplink,
        "expires_in": 600
    }


async def confirm_wa_verification(db: AsyncSession, nonce: str, sender_phone: str) -> dict:
    sender_phone = sender_phone.strip().replace(" ", "").replace("+", "")
    
    stmt = select(OTPCode).where(OTPCode.nonce == nonce)
    res = await db.execute(stmt)
    otp = res.scalars().first()
    
    if not otp:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Nonce verifikasi tidak ditemukan."
        )
        
    if otp.is_used:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Nonce ini sudah digunakan."
        )
        
    otp_expires = otp.expires_at
    if otp_expires.tzinfo is None:
        otp_expires = otp_expires.replace(tzinfo=timezone.utc)
        
    if otp_expires < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Nonce verifikasi telah kedaluwarsa."
        )
        
    # Valid, update DB
    verify_token = str(uuid.uuid4())
    otp.phone_number = sender_phone
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
        
    if otp.is_verified:
        return {
            "status": "verified",
            "verify_token": otp.verify_token
        }
    else:
        return {
            "status": "pending"
        }


# ── OTP FLOWS ────────────────────────────────────────────────────────────────

async def send_otp(db: AsyncSession, target: str, channel: str, purpose: str) -> dict:
    """Generate and store mock OTP code ('7777') for development"""
    # Generate unique ID (UUID)
    otp_id = str(uuid.uuid4())
    
    # Hash of "7777"
    code_hash = hash_password("7777")
    
    # Expiry set to 5 minutes
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    
    # Save in DB
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
    """Verify OTP (hardcoded '7777' check) and issue single-use verify_token"""
    otp = await otp_repo.get_otp_by_id(db, otp_id)
    
    if not otp:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OTP request not found"
        )
        
    if otp.is_used:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OTP code has already been used"
        )
        
    # Ensure datetime has UTC timezone for comparison if needed
    otp_expires = otp.expires_at
    if otp_expires.tzinfo is None:
        otp_expires = otp_expires.replace(tzinfo=timezone.utc)
        
    if otp_expires < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OTP code has expired"
        )
        
    # Standard check: code must be "7777" (or verify hash if "7777" was hashed)
    if code != "7777" and not verify_password(code, otp.code_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid OTP code"
        )
        
    # Mark OTP as used in database
    await otp_repo.mark_otp_used(db, otp)
    
    # Generate single-use verify token
    verify_token = generate_verify_token(otp.target, otp.purpose)
    
    return {
        "verify_token": verify_token,
        "target": otp.target
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
        "phone": buyer.phone
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
        "phone": buyer.phone
    }


async def login_buyer_phone(db: AsyncSession, phone: str, password: str) -> dict:
    """Login buyer via phone number and password"""
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
        "phone": buyer.phone
    }


async def login_buyer_otp(db: AsyncSession, phone: str, verify_token: str) -> dict:
    """Login buyer via OTP verification token"""
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
        "phone": buyer.phone
    }


async def reset_buyer_password(db: AsyncSession, verify_token: str, new_password: str) -> dict:
    """Reset buyer password via verification token"""
    # Try looking up in DB first
    stmt = select(OTPCode).where(OTPCode.verify_token == verify_token)
    res = await db.execute(stmt)
    otp = res.scalars().first()
    
    if otp:
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
        target = otp.phone_number
        otp.is_used = True
        await db.commit()
    else:
        # Fallback to in-memory tokens
        target = consume_verify_token(verify_token, "reset_password")
    
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
    target = consume_verify_token(verify_token, "reset_password")
    
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
