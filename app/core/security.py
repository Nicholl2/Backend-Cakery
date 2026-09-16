import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
import bcrypt
import jwt
from fastapi import HTTPException, status
from app.core.config import settings

# JWT configuration
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = settings.access_token_expire_minutes  # 60 minutes (1 hour)


def hash_password(password: str) -> str:
    """Hash password using bcrypt"""
    password_bytes = password.encode("utf-8")
    return bcrypt.hashpw(password_bytes, bcrypt.gensalt()).decode("utf-8")


get_password_hash = hash_password


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify plain password against hashed password"""
    password_bytes = plain_password.encode("utf-8")
    hashed_bytes = hashed_password.encode("utf-8")
    return bcrypt.checkpw(password_bytes, hashed_bytes)


def create_access_token(
    user_id: int,
    role_level: int,
    username: str,
    expires_delta: Optional[timedelta] = None,
    role: Optional[str] = None
) -> str:
    """Create JWT access token"""
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode = {
        "sub": str(user_id),
        "role_level": role_level,
        "username": username,
        "exp": expire,
        "jti": str(uuid.uuid4())
    }
    
    if role:
        to_encode["role"] = role
    elif role_level == 0:
        to_encode["role"] = "buyer"

    encoded_jwt = jwt.encode(to_encode, settings.secret_key, algorithm=ALGORITHM)
    return encoded_jwt


def revoke_token(token: str, payload: Optional[dict] = None) -> None:
    """
    Blacklist a JWT token until its expiration time using in-memory TTLCache.
    Stores by JTI (if present) and token signature.
    """
    try:
        from app.core.cache import app_cache
    except ImportError:
        return

    if payload is None:
        try:
            payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        except Exception:
            payload = {}

    exp = payload.get("exp")
    if exp:
        now_ts = datetime.now(timezone.utc).timestamp()
        remaining_ttl = int(exp - now_ts)
    else:
        remaining_ttl = ACCESS_TOKEN_EXPIRE_MINUTES * 60

    if remaining_ttl <= 0:
        remaining_ttl = 60  # minimum 1 minute guard

    # 1. Blacklist by JTI if present
    jti = payload.get("jti")
    if jti:
        app_cache.set(f"blacklist:jti:{jti}", True, ttl=remaining_ttl)

    # 2. Blacklist by Signature (3rd segment of JWT)
    parts = token.split(".")
    if len(parts) == 3:
        signature = parts[2]
        app_cache.set(f"blacklist:sig:{signature}", True, ttl=remaining_ttl)

    # 3. Blacklist full token
    app_cache.set(f"blacklist:token:{token}", True, ttl=remaining_ttl)


def is_token_blacklisted(token: str, payload: Optional[dict] = None) -> bool:
    """
    Check if a JWT token has been revoked / blacklisted.
    """
    try:
        from app.core.cache import app_cache
    except ImportError:
        return False

    if payload:
        jti = payload.get("jti")
        if jti and app_cache.get(f"blacklist:jti:{jti}"):
            return True

    parts = token.split(".")
    if len(parts) == 3:
        signature = parts[2]
        if app_cache.get(f"blacklist:sig:{signature}"):
            return True

    if app_cache.get(f"blacklist:token:{token}"):
        return True

    return False


def decode_token(token: str) -> dict:
    """Decode and validate JWT token, checking revocation blacklist"""
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: missing user_id",
                headers={"WWW-Authenticate": "Bearer"},
            )

        if is_token_blacklisted(token, payload):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has been revoked",
                headers={"WWW-Authenticate": "Bearer"},
            )

        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )
