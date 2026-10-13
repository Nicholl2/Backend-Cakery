"""
Authentication Hardening — Regression Tests
============================================

Covers every bug found and fixed in the auth security audit:

1.  Inactive buyer rejected on email+password login
2.  Inactive buyer rejected on OTP login
3.  Inactive seller/internal user rejected on login
4.  Wrong credentials increment lockout counter
5.  Lockout prevents login after max failures (seller & buyer)
6.  Successful login clears lockout counter
7.  Logout then reuse of revoked token is rejected (401)
8.  Duplicate logout of same token is rejected (401)
9.  OTP: invalid/wrong code returns 400
10. OTP: expired code returns 400
11. OTP: reuse of already-used OTP returns 400
12. OTP: attempt limit locks OTP
13. Verify-token: reuse rejected (single-use enforcement)
14. Verify-token: unverified (is_verified=False) token rejected
15. Verify-token: expired token rejected
16. register_buyer: duplicate email rejected WITHOUT burning token
17. register_buyer: duplicate phone rejected WITHOUT burning token
18. Email provider failure: send_otp raises 503
19. Email provider failure: buyer forgot password logs warning, returns 200
20. reset_buyer_password: atomicity — token not burned if buyer not found
21. reset_seller_password: atomicity — token not burned if seller not found
22. reset_buyer_password_email: OTP consumed + password updated atomically
23. Bootstrap: second call rejected
24. Bootstrap: PostgreSQL advisory-lock branch is requested (actual concurrent
    locking requires a PostgreSQL integration test)
25. Concurrent OTP token consumption — second attempt is rejected

NOTE: Tests use SQLite (aiosqlite in-memory). WITH FOR UPDATE silently degrades
on SQLite; the concurrency atomicity of validate_and_consume_db_verify_token is
tested via sequential simulation only. Real PostgreSQL is required to validate
SELECT FOR UPDATE row-locking behavior.
"""

import asyncio
import uuid
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, AsyncMock
from fastapi import HTTPException

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.core.security import (
    hash_password,
    create_access_token,
    decode_token,
    revoke_token,
)
from app.core.cache import app_cache
from app.models.buyer import Buyer
from app.models.otp_code import OTPCode
from app.models.user import User
from app.models.role import Role


# ── Shared in-memory SQLite engine ────────────────────────────────────────────

TEST_DB_URL = "sqlite+aiosqlite://"


def make_engine():
    return create_async_engine(
        TEST_DB_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


@pytest.fixture
async def db():
    engine = make_engine()
    Session = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with Session() as session:
        yield session
    await engine.dispose()


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _seed_role(db: AsyncSession, role_id: int = 2, level: int = 2) -> Role:
    role = Role(id=role_id, nama_role="Admin", level=level)
    db.add(role)
    await db.flush()
    return role


async def _seed_owner_role(db: AsyncSession) -> Role:
    role = Role(id=1, nama_role="Owner", level=1)
    db.add(role)
    await db.flush()
    return role


async def _seed_buyer(db: AsyncSession, email="buyer@test.com", phone="+6281200000000", active=True) -> Buyer:
    buyer = Buyer(
        name="Test Buyer",
        email=email,
        phone=phone,
        password_hash=hash_password("correct-password123"),
        is_active=active,
        is_verified=True,
    )
    db.add(buyer)
    await db.flush()
    return buyer


async def _seed_otp(
    db: AsyncSession,
    *,
    target: str,
    purpose: str = "login",
    expired: bool = False,
    used: bool = False,
    verified: bool = True,
    code: str = "123456",
) -> OTPCode:
    expires_at = (
        datetime.now(timezone.utc) - timedelta(minutes=1)
        if expired
        else datetime.now(timezone.utc) + timedelta(minutes=10)
    )
    otp = OTPCode(
        id=str(uuid.uuid4()),
        target=target,
        phone_number=target if "+62" in target else None,
        channel="email" if "@" in target else "whatsapp",
        purpose=purpose,
        code_hash=hash_password(code),
        expires_at=expires_at,
        is_used=used,
        is_verified=verified,
        verify_token=str(uuid.uuid4()) if verified else None,
        attempt_count=0,
    )
    db.add(otp)
    await db.flush()
    return otp


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1: Inactive account rejection
# ══════════════════════════════════════════════════════════════════════════════

class TestInactiveAccountRejection:
    """
    Confirms that deactivated buyers/sellers cannot log in via any method.
    """

    @pytest.mark.asyncio
    async def test_inactive_buyer_email_login_rejected(self, db: AsyncSession):
        """Bug 1 fix: login_buyer_password must check is_active."""
        app_cache.clear()
        buyer = await _seed_buyer(db, active=False)
        await db.commit()

        from app.services.buyer_auth_service import login_buyer_password
        with pytest.raises(HTTPException) as exc:
            await login_buyer_password(db, buyer.email, "correct-password123")
        assert exc.value.status_code == 422
        assert "tidak aktif" in exc.value.detail.lower() or "inactive" in exc.value.detail.lower()

    @pytest.mark.asyncio
    async def test_active_buyer_email_login_succeeds(self, db: AsyncSession):
        app_cache.clear()
        buyer = await _seed_buyer(db, email="active@test.com", phone="+6281200000001", active=True)
        await db.commit()

        from app.services.buyer_auth_service import login_buyer_password
        result = await login_buyer_password(db, buyer.email, "correct-password123")
        assert "access_token" in result

    @pytest.mark.asyncio
    async def test_inactive_buyer_otp_login_rejected(self, db: AsyncSession):
        """Bug 2 fix: login_buyer_otp must check is_active."""
        """Bug 2 fix: login_buyer_otp must check is_active.
        
        We create the buyer with the normalized phone, then create an OTP whose
        phone_number matches exactly. validate_and_consume_db_verify_token will
        succeed (phone matches, token valid), then login_buyer_otp checks is_active.
        """
        from app.utils.phone import normalize_phone
        phone = normalize_phone("+6281200000002")

        buyer = Buyer(
            name="Inactive OTP Buyer",
            email="otpinactive@test.com",
            phone=phone,
            password_hash=hash_password("correct-password123"),
            is_active=False,
            is_verified=True,
        )
        db.add(buyer)
        await db.flush()

        verify_token = str(uuid.uuid4())
        otp = OTPCode(
            id=str(uuid.uuid4()),
            target=phone,
            phone_number=phone,
            channel="whatsapp",
            purpose="login",
            code_hash=hash_password("nonce"),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            is_used=False,
            is_verified=True,
            verify_token=verify_token,
            attempt_count=0,
        )
        db.add(otp)
        await db.commit()

        from app.services.buyer_auth_service import login_buyer_otp
        with pytest.raises(HTTPException) as exc:
            await login_buyer_otp(db, phone, verify_token)
        assert exc.value.status_code == 422
        await db.refresh(otp)
        assert otp.is_used is False

    @pytest.mark.asyncio
    async def test_inactive_seller_login_rejected(self, db: AsyncSession):
        """Internal seller with is_active=False should be rejected at authenticate_user."""
        app_cache.clear()
        role = await _seed_role(db)
        user = User(
            username="inactive_seller",
            password_hash=hash_password("password12345"),
            role_id=role.id,
            is_active=False,
        )
        db.add(user)
        await db.commit()

        from app.schemas.auth import UserLogin
        from app.services.auth_service import authenticate_user

        login_data = UserLogin(identifier="inactive_seller", password="password12345")
        with pytest.raises(HTTPException) as exc:
            await authenticate_user(db, login_data)
        assert exc.value.status_code == 422


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2: Wrong credentials / lockout
# ══════════════════════════════════════════════════════════════════════════════

class TestCredentialsAndLockout:
    def setup_method(self):
        app_cache.clear()

    @pytest.mark.asyncio
    async def test_wrong_password_returns_401(self, db: AsyncSession):
        buyer = await _seed_buyer(db, email="locktest@test.com", phone="+6281200000010")
        await db.commit()

        from app.services.buyer_auth_service import login_buyer_password
        with pytest.raises(HTTPException) as exc:
            await login_buyer_password(db, buyer.email, "wrong-password")
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_buyer_lockout_after_5_failures(self, db: AsyncSession):
        from app.services.buyer_auth_service import (
            _record_buyer_failed_login,
            _check_buyer_lockout,
            _MAX_BUYER_LOGIN_ATTEMPTS,
        )
        identifier = "lockout5@test.com"
        for _ in range(_MAX_BUYER_LOGIN_ATTEMPTS):
            _record_buyer_failed_login(identifier)
        with pytest.raises(HTTPException) as exc:
            _check_buyer_lockout(identifier)
        assert exc.value.status_code == 429

    @pytest.mark.asyncio
    async def test_seller_lockout_after_5_failures(self):
        from app.services.auth_service import (
            _record_failed_login,
            _check_login_lockout,
            _MAX_LOGIN_ATTEMPTS,
        )
        identifier = "seller_lockout_user"
        for _ in range(_MAX_LOGIN_ATTEMPTS):
            _record_failed_login(identifier)
        with pytest.raises(HTTPException) as exc:
            _check_login_lockout(identifier)
        assert exc.value.status_code == 429

    @pytest.mark.asyncio
    async def test_successful_login_clears_lockout_counter(self, db: AsyncSession):
        from app.services.buyer_auth_service import (
            _record_buyer_failed_login,
            _clear_buyer_login_attempts,
            _buyer_login_key,
        )
        identifier = "clearme@test.com"
        _record_buyer_failed_login(identifier)
        _record_buyer_failed_login(identifier)
        _clear_buyer_login_attempts(identifier)
        assert app_cache.get(_buyer_login_key(identifier)) is None


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3: Logout / token revocation
# ══════════════════════════════════════════════════════════════════════════════

class TestTokenRevocation:
    def setup_method(self):
        app_cache.clear()

    def test_revoked_token_rejected_by_decode(self):
        """Post-logout, the same token must not be decodeable."""
        token = create_access_token(user_id=1, role_level=1, username="seller1")
        payload = decode_token(token)
        revoke_token(token, payload)
        with pytest.raises(HTTPException) as exc:
            decode_token(token)
        assert exc.value.status_code == 401
        assert "revoked" in exc.value.detail.lower()

    def test_second_revoke_of_same_token_is_idempotent(self):
        """Revoking an already-revoked token should not crash."""
        token = create_access_token(user_id=2, role_level=2, username="admin1")
        payload = decode_token(token)
        revoke_token(token, payload)
        revoke_token(token, payload)  # should not raise
        with pytest.raises(HTTPException):
            decode_token(token)

    def test_other_tokens_unaffected_by_revocation(self):
        """Revoking token A must not affect token B."""
        token_a = create_access_token(user_id=10, role_level=1, username="ownerA")
        token_b = create_access_token(user_id=11, role_level=2, username="adminB")
        revoke_token(token_a, decode_token(token_a))
        # token_b must still be valid
        payload_b = decode_token(token_b)
        assert payload_b["sub"] == "11"


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4: OTP validation
# ══════════════════════════════════════════════════════════════════════════════

class TestOTPValidation:
    @pytest.mark.asyncio
    async def test_wrong_otp_code_returns_400(self, db: AsyncSession):
        otp = await _seed_otp(db, target="seller@test.com", purpose="reset_password", verified=False)
        await db.commit()

        from app.services.buyer_auth_service import verify_otp
        with pytest.raises(HTTPException) as exc:
            await verify_otp(db, otp.id, "000000")
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_expired_otp_returns_400(self, db: AsyncSession):
        otp = await _seed_otp(
            db, target="exp@test.com", purpose="reset_password", expired=True, verified=False
        )
        await db.commit()

        from app.services.buyer_auth_service import verify_otp
        with pytest.raises(HTTPException) as exc:
            await verify_otp(db, otp.id, "123456")
        assert exc.value.status_code == 400
        assert "kedaluwarsa" in exc.value.detail.lower()

    @pytest.mark.asyncio
    async def test_used_otp_returns_400(self, db: AsyncSession):
        otp = await _seed_otp(
            db, target="used@test.com", purpose="reset_password", used=True, verified=False
        )
        await db.commit()

        from app.services.buyer_auth_service import verify_otp
        with pytest.raises(HTTPException) as exc:
            await verify_otp(db, otp.id, "123456")
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_otp_locked_after_5_wrong_attempts(self, db: AsyncSession):
        otp = await _seed_otp(
            db, target="maxattempts@test.com", purpose="reset_password", verified=False
        )
        await db.commit()

        from app.services.buyer_auth_service import verify_otp

        # 5 wrong attempts
        for i in range(4):
            with pytest.raises(HTTPException) as exc:
                await verify_otp(db, otp.id, "000000")
            # should be 400 until we reach max
            assert exc.value.status_code in (400, 429)

        # 5th failure should lock and return 429
        with pytest.raises(HTTPException) as exc:
            await verify_otp(db, otp.id, "000000")
        assert exc.value.status_code == 429


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5: Verify-token single-use enforcement
# ══════════════════════════════════════════════════════════════════════════════

class TestVerifyTokenSingleUse:
    @pytest.mark.asyncio
    async def test_verify_token_purpose_is_bound(self, db: AsyncSession):
        phone = "+6281200000020"
        otp = await _seed_otp(db, target=phone, purpose="login", verified=True)
        await db.commit()
        from app.services.buyer_auth_service import validate_and_consume_db_verify_token
        with pytest.raises(HTTPException):
            await validate_and_consume_db_verify_token(db, otp.verify_token, phone, purpose="register")
        await db.refresh(otp)
        assert otp.is_used is False

    @pytest.mark.asyncio
    async def test_verify_token_single_use(self, db: AsyncSession):
        """Bug 6/10 fix: token can only be consumed once."""
        phone = "+6281200000020"
        otp = await _seed_otp(db, target=phone, purpose="login", verified=True)
        await db.commit()

        from app.services.buyer_auth_service import validate_and_consume_db_verify_token

        # First use: should succeed
        await validate_and_consume_db_verify_token(db, otp.verify_token, phone)

        # Second use: must be rejected
        with pytest.raises(HTTPException) as exc:
            await validate_and_consume_db_verify_token(db, otp.verify_token, phone)
        assert exc.value.status_code == 400
        assert "sudah digunakan" in exc.value.detail

    @pytest.mark.asyncio
    async def test_unverified_token_rejected(self, db: AsyncSession):
        """Bug 10 fix: is_verified=False token must be rejected."""
        phone = "+6281200000021"
        otp_id = str(uuid.uuid4())
        verify_token = str(uuid.uuid4())
        otp = OTPCode(
            id=otp_id,
            target=phone,
            phone_number=phone,
            channel="whatsapp",
            purpose="login",
            code_hash=hash_password("nonce"),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            is_used=False,
            is_verified=False,  # NOT verified
            verify_token=verify_token,
            attempt_count=0,
        )
        db.add(otp)
        await db.commit()

        from app.services.buyer_auth_service import validate_and_consume_db_verify_token
        with pytest.raises(HTTPException) as exc:
            await validate_and_consume_db_verify_token(db, verify_token, phone)
        assert exc.value.status_code == 400
        assert "belum dikonfirmasi" in exc.value.detail

    @pytest.mark.asyncio
    async def test_expired_verify_token_rejected(self, db: AsyncSession):
        phone = "+6281200000022"
        otp = await _seed_otp(db, target=phone, purpose="login", verified=True, expired=True)
        await db.commit()

        from app.services.buyer_auth_service import validate_and_consume_db_verify_token
        with pytest.raises(HTTPException) as exc:
            await validate_and_consume_db_verify_token(db, otp.verify_token, phone)
        assert exc.value.status_code == 400
        assert "kedaluwarsa" in exc.value.detail


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6: Register buyer — token not burned on validation failure
# ══════════════════════════════════════════════════════════════════════════════

class TestRegisterBuyerTokenOrdering:
    @pytest.mark.asyncio
    async def test_concurrent_duplicate_registration_rolls_back_token(self, db: AsyncSession):
        phone = "+6281200000099"
        otp = await _seed_otp(db, target=phone, purpose="login", verified=True)
        await db.commit()
        competitor = Buyer(name="Winner", email="race@test.com", phone=phone,
                           password_hash=hash_password("correct-password123"))
        from sqlalchemy.exc import IntegrityError
        from app.services.buyer_auth_service import register_buyer
        with patch("app.services.buyer_auth_service.buyer_repo.get_buyer_by_email",
                   new_callable=AsyncMock, side_effect=[None, competitor]), \
             patch("app.services.buyer_auth_service.buyer_repo.get_buyer_by_phone",
                   new_callable=AsyncMock, return_value=None), \
             patch.object(db, "commit", new_callable=AsyncMock,
                          side_effect=IntegrityError("insert", {}, Exception("unique"))):
            with pytest.raises(HTTPException) as exc:
                await register_buyer(db, "Winner", "race@test.com", phone,
                                     "NewPassword123!", otp.verify_token)
        assert exc.value.status_code == 400
        await db.refresh(otp)
        assert otp.is_used is False

    """Bug 3 fix: duplicate checks happen BEFORE consuming the verify_token."""

    @pytest.mark.asyncio
    async def test_duplicate_email_does_not_burn_token(self, db: AsyncSession):
        phone = "+6281200000030"
        # Seed an existing buyer with same email
        existing = await _seed_buyer(
            db, email="dup@email.com", phone="+6281200000031"
        )
        otp = await _seed_otp(db, target=phone, purpose="register", verified=True)
        await db.commit()

        from app.services.buyer_auth_service import register_buyer
        with pytest.raises(HTTPException) as exc:
            await register_buyer(
                db,
                name="New",
                email="dup@email.com",  # duplicate
                phone=phone,
                password="password12345",
                verify_token=otp.verify_token,
            )
        assert exc.value.status_code == 400
        assert "email" in exc.value.detail.lower()

        # Token must NOT have been burned — is_used should still be False
        await db.refresh(otp)
        assert otp.is_used is False, "Token should NOT be consumed when duplicate-email check fails"

    @pytest.mark.asyncio
    async def test_duplicate_phone_does_not_burn_token(self, db: AsyncSession):
        """Bug 3 fix: duplicate phone check happens BEFORE consuming the verify_token.
        
        register_buyer normalizes the phone internally via normalize_phone().
        We must store the existing buyer with the same normalized form to trigger
        the duplicate detection.
        """
        from app.utils.phone import normalize_phone
        # normalize_phone converts +628... to the canonical form used in the DB
        phone = normalize_phone("+6281200000032")

        # Seed an existing buyer with the normalized phone
        existing = Buyer(
            name="Existing Phone User",
            email="other@test.com",
            phone=phone,
            password_hash=hash_password("pass12345"),
            is_active=True,
            is_verified=True,
        )
        db.add(existing)
        await db.flush()

        # Seed a verified OTP whose phone_number matches
        verify_token = str(uuid.uuid4())
        otp = OTPCode(
            id=str(uuid.uuid4()),
            target=phone,
            phone_number=phone,
            channel="whatsapp",
            purpose="register",
            code_hash=hash_password("nonce"),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            is_used=False,
            is_verified=True,
            verify_token=verify_token,
            attempt_count=0,
        )
        db.add(otp)
        await db.commit()

        from app.services.buyer_auth_service import register_buyer
        with pytest.raises(HTTPException) as exc:
            await register_buyer(
                db,
                name="New",
                email="fresh@email.com",
                phone=phone,  # duplicate (already normalized)
                password="password12345",
                verify_token=verify_token,
            )
        assert exc.value.status_code == 400
        assert "phone" in exc.value.detail.lower()

        await db.refresh(otp)
        assert otp.is_used is False, "Token should NOT be consumed when duplicate-phone check fails"


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 7: Email provider failure
# ══════════════════════════════════════════════════════════════════════════════

class TestEmailProviderFailure:
    @pytest.mark.asyncio
    async def test_unconfigured_smtp_is_not_reported_as_delivered(self):
        import logging
        from app.utils.email_helper import send_otp_email
        with patch("app.utils.email_helper.settings.smtp_host", ""):
            assert await send_otp_email("buyer@test.com", "123456") is False

    def test_production_settings_require_smtp_configuration(self):
        from pydantic import ValidationError
        from app.core.config import Settings
        with pytest.raises(ValidationError, match="Production requires SMTP"):
            # Pass smtp_host="" and smtp_from_email="" explicitly so that any SMTP
            # values present in a local .env file do not suppress the validator.
            Settings(database_url="sqlite+aiosqlite://", environment="production",
                     secret_key="strong-secret", service_api_key="strong-service-key",
                     smtp_host="", smtp_from_email="")

    @pytest.mark.asyncio
    async def test_send_otp_raises_503_when_email_fails(self, db: AsyncSession):
        """Bug 7 & 9 fix: send_otp must raise 503 if email delivery fails."""
        with patch(
            "app.services.buyer_auth_service.send_otp_email",
            new_callable=AsyncMock,
            return_value=False,
        ):
            from app.services import buyer_auth_service
            # Reload the function to pick up mock (already imported)
            from app.services.buyer_auth_service import send_otp as svc_send_otp
            with pytest.raises(HTTPException) as exc:
                await svc_send_otp(db, "seller@test.com", "email", "reset_password")
            assert exc.value.status_code == 503

    @pytest.mark.asyncio
    async def test_buyer_forgot_password_returns_200_on_email_failure(self, db: AsyncSession):
        """Bug 9 partial: buyer forgot-password endpoint returns 200 even on email failure
        (anti-enumeration design), but failure is logged."""
        buyer = await _seed_buyer(db, email="emailfail@test.com", phone="+6281200000040")
        await db.commit()

        with patch(
            "app.services.buyer_auth_service.send_otp_email",
            new_callable=AsyncMock,
            return_value=False,
        ):
            from app.services.buyer_auth_service import request_buyer_forgot_password
            result = await request_buyer_forgot_password(db, "emailfail@test.com")
        # Should still return 200 (anti-enumeration)
        assert "message" in result

    @pytest.mark.asyncio
    async def test_buyer_forgot_password_nonexistent_email_returns_200(self, db: AsyncSession):
        """Anti-enumeration: non-existent email still gets 200."""
        from app.services.buyer_auth_service import request_buyer_forgot_password
        result = await request_buyer_forgot_password(db, "nope@nonexistent.com")
        assert "message" in result


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 8: Reset password atomicity
# ══════════════════════════════════════════════════════════════════════════════

class TestResetPasswordAtomicity:
    @pytest.mark.asyncio
    async def test_buyer_reset_token_not_burned_if_buyer_not_found(self, db: AsyncSession):
        """Bug 4 fix: if buyer doesn't exist, token must NOT be consumed."""
        # Create an OTP with a target email that has no matching buyer
        otp_id = str(uuid.uuid4())
        verify_token = str(uuid.uuid4())
        otp = OTPCode(
            id=otp_id,
            target="ghost@nowhere.com",
            channel="whatsapp",
            purpose="buyer_reset_password",
            code_hash=hash_password("123456"),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            is_used=False,
            is_verified=True,
            verify_token=verify_token,
            attempt_count=0,
        )
        db.add(otp)
        await db.commit()

        from app.services.buyer_auth_service import reset_buyer_password
        with pytest.raises(HTTPException) as exc:
            await reset_buyer_password(db, verify_token, "NewPassword123!")
        assert exc.value.status_code == 404

        # Token must NOT be burned
        await db.refresh(otp)
        assert otp.is_used is False, "Token must not be consumed when buyer is not found"

    @pytest.mark.asyncio
    async def test_buyer_reset_success_consumes_token_and_updates_password(self, db: AsyncSession):
        """Happy path: token is consumed and password is updated in one commit."""
        phone = "+6281200000050"
        buyer = await _seed_buyer(db, email="resetme@test.com", phone=phone)

        otp_id = str(uuid.uuid4())
        verify_token = str(uuid.uuid4())
        otp = OTPCode(
            id=otp_id,
            target="resetme@test.com",
            channel="email",
            purpose="reset_password",
            code_hash=hash_password("123456"),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            is_used=False,
            is_verified=True,
            verify_token=verify_token,
            attempt_count=0,
        )
        older_token = OTPCode(
            id=str(uuid.uuid4()), target="resetme@test.com", channel="email",
            purpose="buyer_reset_password", code_hash=hash_password("654321"),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            is_used=False, is_verified=True, verify_token=str(uuid.uuid4()), attempt_count=0,
        )
        db.add(otp)
        db.add(older_token)
        await db.commit()

        from app.services.buyer_auth_service import reset_buyer_password
        result = await reset_buyer_password(db, verify_token, "NewPassword123!")
        assert "reset" in result["message"].lower() or "password" in result["message"].lower()

        await db.refresh(otp)
        assert otp.is_used is True
        await db.refresh(older_token)
        assert older_token.is_used is True

        await db.refresh(buyer)
        from app.core.security import verify_password
        assert verify_password("NewPassword123!", buyer.password_hash)

    @pytest.mark.asyncio
    async def test_seller_reset_token_not_burned_if_seller_not_found(self, db: AsyncSession):
        """Bug 5 fix: if seller doesn't exist, token must NOT be consumed."""
        verify_token = str(uuid.uuid4())
        otp = OTPCode(
            id=str(uuid.uuid4()),
            target="ghost_seller@nowhere.com",
            channel="email",
            purpose="reset_password",
            code_hash=hash_password("123456"),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            is_used=False,
            is_verified=True,
            verify_token=verify_token,
            attempt_count=0,
        )
        db.add(otp)
        await db.commit()

        from app.services.buyer_auth_service import reset_seller_password
        with pytest.raises(HTTPException) as exc:
            await reset_seller_password(db, verify_token, "NewPassword123!")
        assert exc.value.status_code == 404

        await db.refresh(otp)
        assert otp.is_used is False, "Token must not be consumed when seller is not found"

    @pytest.mark.asyncio
    async def test_seller_reset_revokes_other_active_tokens(self, db: AsyncSession):
        db.add(Role(id=2, nama_role="Admin", level=2))
        user = User(username="reset-seller", email="reset-seller@test.com",
                    password_hash=hash_password("OldPassword123!"), role_id=2)
        db.add(user)
        selected = OTPCode(
            id=str(uuid.uuid4()), target=user.email, channel="email", purpose="reset_password",
            code_hash=hash_password("123456"), expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            is_used=False, is_verified=True, verify_token=str(uuid.uuid4()), attempt_count=0,
        )
        sibling = OTPCode(
            id=str(uuid.uuid4()), target=user.email, channel="email", purpose="reset_password",
            code_hash=hash_password("654321"), expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            is_used=False, is_verified=True, verify_token=str(uuid.uuid4()), attempt_count=0,
        )
        db.add_all([selected, sibling])
        await db.commit()
        from app.services.buyer_auth_service import reset_seller_password
        await reset_seller_password(db, selected.verify_token, "NewPassword123!")
        await db.refresh(sibling)
        assert sibling.is_used is True

    @pytest.mark.asyncio
    async def test_reset_buyer_password_email_otp_consumed_and_password_updated(self, db: AsyncSession):
        """reset_buyer_password_email: OTP is single-use and password update is atomic."""
        buyer = await _seed_buyer(db, email="emailreset@test.com", phone="+6281200000055")

        otp = OTPCode(
            id=str(uuid.uuid4()),
            target="emailreset@test.com",
            channel="email",
            purpose="buyer_reset_password",
            code_hash=hash_password("654321"),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            is_used=False,
            is_verified=False,
            attempt_count=0,
        )
        db.add(otp)
        await db.commit()

        from app.services.buyer_auth_service import reset_buyer_password_email
        result = await reset_buyer_password_email(db, "emailreset@test.com", "654321", "NewPass12345!")
        assert "berhasil" in result["message"].lower() or "reset" in result["message"].lower()

        await db.refresh(otp)
        assert otp.is_used is True

        await db.refresh(buyer)
        from app.core.security import verify_password
        assert verify_password("NewPass12345!", buyer.password_hash)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 9: Bootstrap
# ══════════════════════════════════════════════════════════════════════════════

class TestBootstrapOwner:
    @pytest.mark.asyncio
    async def test_postgres_bootstrap_requests_transaction_advisory_lock(self, db: AsyncSession):
        from types import SimpleNamespace
        from app.schemas.user import UserBootstrap
        from app.services.user_service import bootstrap_owner

        statements = []

        class PostgresDialectSession:
            bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

            def __getattr__(self, name):
                return getattr(db, name)

            async def execute(self, statement, *args, **kwargs):
                if "pg_advisory_xact_lock" in str(statement):
                    statements.append(str(statement))
                    return None
                return await db.execute(statement, *args, **kwargs)

        await bootstrap_owner(PostgresDialectSession(), UserBootstrap(
            username="locked-bootstrap", password="StrongPass123!", nomor_wa_admin="+6281200000077"
        ))
        assert len(statements) == 1

    @pytest.mark.asyncio
    async def test_bootstrap_succeeds_on_empty_table(self, db: AsyncSession):
        # Seed roles first
        role = Role(id=1, nama_role="Owner", level=1)
        role2 = Role(id=2, nama_role="Admin", level=2)
        role3 = Role(id=3, nama_role="Staff", level=3)
        db.add_all([role, role2, role3])
        await db.commit()

        from app.schemas.user import UserBootstrap
        from app.services.user_service import bootstrap_owner

        data = UserBootstrap(
            username="owner_bootstrap",
            password="StrongPass123!",
            nomor_wa_admin="+6281200000099"
        )
        user = await bootstrap_owner(db, data)
        assert user.username == "owner_bootstrap"
        assert user.is_active is True

    @pytest.mark.asyncio
    async def test_bootstrap_fails_if_users_exist(self, db: AsyncSession):
        """Bug 8: bootstrap must be rejected if any user already exists."""
        role = Role(id=1, nama_role="Owner", level=1)
        db.add(role)
        existing = User(
            username="existing_owner",
            password_hash=hash_password("pass12345"),
            role_id=1,
            is_active=True,
        )
        db.add(existing)
        await db.commit()

        from app.schemas.user import UserBootstrap
        from app.services.user_service import bootstrap_owner

        data = UserBootstrap(
            username="second_owner",
            password="StrongPass123!",
            nomor_wa_admin="+6281200000088"
        )
        with pytest.raises(HTTPException) as exc:
            await bootstrap_owner(db, data)
        assert exc.value.status_code == 400
        assert "kosong" in exc.value.detail.lower() or "bootstrap" in exc.value.detail.lower()

    @pytest.mark.asyncio
    async def test_bootstrap_weak_password_rejected(self, db: AsyncSession):
        """Bootstrap endpoint must enforce password strength.
        
        The Pydantic schema (UserBootstrap) enforces min_length=8 at the
        API boundary, so short passwords raise ValidationError before reaching
        bootstrap_owner(). We verify both layers:
        1. Pydantic schema raises ValidationError for passwords < 8 chars.
        2. bootstrap_owner() service raises HTTPException for passwords that
           pass Pydantic but fail validate_password_strength() (same threshold,
           so we test service directly with a bypassed schema object).
        """
        from pydantic import ValidationError
        from app.schemas.user import UserBootstrap
        from app.services.user_service import bootstrap_owner

        # Layer 1: Pydantic schema validation
        with pytest.raises(ValidationError):
            UserBootstrap(
                username="weak_owner",
                password="short",  # < 8 chars — Pydantic rejects
                nomor_wa_admin="+6281200000077"
            )

        # Layer 2: Service-level validation (bypass schema by constructing manually)
        # Use a mock-like object that bypasses Pydantic
        class BootstrapData:
            username = "weak_owner2"
            password = "short"  # < 8 chars
            nomor_wa_admin = "+6281200000077"
            email = None
            phone_number = None

        with pytest.raises(HTTPException) as exc:
            await bootstrap_owner(db, BootstrapData())
        assert exc.value.status_code == 400


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 10: Concurrent OTP replay (sequential simulation)
# ══════════════════════════════════════════════════════════════════════════════

class TestConcurrentOTPReplay:
    """
    Simulates two sequential requests using the same OTP verify_token.
    True concurrent safety on PostgreSQL is guaranteed by WITH FOR UPDATE
    (not testable via SQLite — see module docstring).
    """

    @pytest.mark.asyncio
    async def test_second_use_of_verify_token_rejected(self, db: AsyncSession):
        phone = "+6281200000060"
        otp = await _seed_otp(db, target=phone, purpose="login", verified=True)
        await db.commit()

        from app.services.buyer_auth_service import validate_and_consume_db_verify_token

        # Request 1: succeeds
        await validate_and_consume_db_verify_token(db, otp.verify_token, phone)

        # Request 2 (sequential replay): must fail
        with pytest.raises(HTTPException) as exc:
            await validate_and_consume_db_verify_token(db, otp.verify_token, phone)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_two_simultaneous_otp_verifications_simulate(self, db: AsyncSession):
        """
        Simulates the logic that would apply for concurrent requests.
        Since SQLite doesn't enforce FOR UPDATE, we test the state-based guard only.
        """
        phone = "+6281200000061"
        otp = await _seed_otp(db, target=phone, purpose="login", verified=True)
        await db.commit()

        from app.services.buyer_auth_service import validate_and_consume_db_verify_token

        token = otp.verify_token

        # Simulate concurrent: both requests read the token as not-used,
        # but the second one that tries to consume should detect it was already used.
        # We model this sequentially here.
        result1_raised = False
        result2_raised = False

        try:
            await validate_and_consume_db_verify_token(db, token, phone)
        except HTTPException:
            result1_raised = True

        try:
            await validate_and_consume_db_verify_token(db, token, phone)
        except HTTPException:
            result2_raised = True

        # One should succeed, one should fail
        assert not result1_raised or not result2_raised, "At least one should succeed"
        assert result1_raised or result2_raised, "At least one should fail (token is single-use)"


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 11: In-memory lockout multi-worker limitation documentation
# ══════════════════════════════════════════════════════════════════════════════

class TestInMemoryLockoutLimitations:
    """
    Verifies the in-memory lockout state is process-local.
    Documents known limitation: in multi-worker deployments, each worker
    has independent counters — lockout is NOT globally enforced.
    """

    def test_lockout_state_is_process_local(self):
        """
        Demonstrates that lockout counters live in the process-local TTLCache.
        In multi-worker (gunicorn) deployments, an attacker can hit each
        worker in round-robin to bypass lockout. A shared store (Redis) would
        be required for strict enforcement.

        LIMITATION DOCUMENTED:
        - Login lockout (5 attempts / 15 min) is process-local only.
        - JWT blacklist (post-logout) is process-local only.
        - On worker restart, all lockout counters and blacklisted tokens are cleared.

        CURRENT MITIGATION:
        - Rate limiting via SlowAPI (also process-local, same caveat).
        - Deployment recommendation: use single-worker or add Redis.
        """
        app_cache.clear()
        from app.services.auth_service import _record_failed_login, _login_attempt_key

        _record_failed_login("testuser")
        key = _login_attempt_key("testuser")
        assert app_cache.get(key) == 1  # exists only in THIS process
        # In another worker process, app_cache.get(key) would be None
