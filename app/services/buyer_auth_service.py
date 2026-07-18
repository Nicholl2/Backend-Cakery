import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, Any

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories import buyer_repo, otp_repo, user_repo
from app.core.security import hash_password, verify_password, create_access_token

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
    # Consume verify token with 'register' purpose
    target = consume_verify_token(verify_token, "register")
    
    # Ensure verification target matches the provided email or phone
    if target != email and target != phone:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Verification token target mismatch"
        )
        
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
    # Consume verify token with 'login' purpose
    target = consume_verify_token(verify_token, "login")
    
    if target != phone:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Verification token target mismatch for phone number"
        )
        
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
    # Consume verify token with 'reset_password' purpose
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
