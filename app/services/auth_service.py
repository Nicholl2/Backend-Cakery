from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, status
from app.repositories import user_repo
from app.core.security import verify_password, create_access_token
from app.core.cache import app_cache
from app.schemas.auth import UserLogin, Token

# ── LOGIN ATTEMPT TRACKING ───────────────────────────────────────────────────
_MAX_LOGIN_ATTEMPTS = 5
_LOCKOUT_SECONDS = 15 * 60  # 15 menit


def _login_attempt_key(identifier: str) -> str:
    return f"login_attempts:seller:{identifier.lower().strip()}"


def _check_login_lockout(identifier: str) -> None:
    """Raise 429 if the identifier has exceeded max login attempts."""
    key = _login_attempt_key(identifier)
    attempts = app_cache.get(key)
    if attempts is not None and attempts >= _MAX_LOGIN_ATTEMPTS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Terlalu banyak percobaan login gagal. "
                f"Akun dikunci sementara selama 15 menit. Silakan coba lagi nanti."
            ),
        )


def _record_failed_login(identifier: str) -> None:
    """Increment failed login attempt counter."""
    key = _login_attempt_key(identifier)
    current = app_cache.get(key) or 0
    app_cache.set(key, current + 1, ttl=_LOCKOUT_SECONDS)


def _clear_login_attempts(identifier: str) -> None:
    """Reset login attempt counter on successful login."""
    key = _login_attempt_key(identifier)
    app_cache.invalidate(key)


async def authenticate_user(db: AsyncSession, login_data: UserLogin) -> Token:
    """
    Authenticate user and return JWT token.
    The `identifier` field accepts username, email, or phone_number.

    Raises:
        HTTPException 429: Too many failed attempts (locked out 15 min)
        HTTPException 401: Invalid credentials
        HTTPException 422: User inactive
    """
    identifier = login_data.identifier

    # Check lockout BEFORE doing any DB lookup
    _check_login_lockout(identifier)

    # Try lookup: username → email → phone_number
    user = await user_repo.get_user_by_username(db, identifier)
    if not user:
        user = await user_repo.get_user_by_email(db, identifier)
    if not user:
        user = await user_repo.get_user_by_phone(db, identifier)

    if not user:
        _record_failed_login(identifier)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Verify password
    if not verify_password(login_data.password, user.password_hash):
        _record_failed_login(identifier)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Check if user is active
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="User account is inactive",
        )

    # Successful login — clear any accumulated failed attempts
    _clear_login_attempts(identifier)

    # Get user role level
    role_level = user.role.level if user.role else 3

    # Create JWT token
    access_token = create_access_token(
        user_id=user.id,
        role_level=role_level,
        username=user.username
    )

    return Token(
        access_token=access_token,
        token_type="bearer",
        user_id=user.id,
        role_level=role_level,
        username=user.username
    )


async def get_current_user_id(db: AsyncSession, token_payload: dict) -> int:
    """Extract and validate user ID from token"""
    user_id = int(token_payload.get("sub"))
    
    # Verify user still exists and is active
    is_active = await user_repo.is_user_active(db, user_id)
    if not is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User is no longer active",
        )

    return user_id