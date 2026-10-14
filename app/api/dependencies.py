from fastapi import Header, Query, Depends, HTTPException, status
from app.core.config import settings
from fastapi.security import HTTPBearer
from fastapi.security.http import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
from app.core.database import get_db
from app.core.security import decode_token
from app.repositories import user_repo
from typing import Optional
import logging
import hmac

logger = logging.getLogger(__name__)

security = HTTPBearer()


async def get_current_user_payload(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> dict:
    """Extract and validate JWT token"""
    token = credentials.credentials
    return decode_token(token)


async def get_current_user_id(
    payload: dict = Depends(get_current_user_payload),
    db: AsyncSession = Depends(get_db)
) -> int:
    """Get current user ID from token and verify user exists"""
    user_id = int(payload.get("sub"))
    is_active = await user_repo.is_user_active(db, user_id)
    
    if not is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User is no longer active"
        )
    
    return user_id


from app.models.buyer import Buyer


async def get_current_buyer(
    payload: dict = Depends(get_current_user_payload),
    db: AsyncSession = Depends(get_db)
) -> Buyer:
    """Get current buyer object from token and verify buyer is active/exists"""
    if payload.get("role") != "buyer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User is not a buyer"
        )
    buyer_id = int(payload.get("sub"))
    
    from app.repositories import buyer_repo
    buyer = await buyer_repo.get_buyer_by_id(db, buyer_id)
    if not buyer or not buyer.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Buyer account is inactive or not found"
        )
    return buyer


async def get_current_buyer_id(
    buyer: Buyer = Depends(get_current_buyer)
) -> int:
    """Get current buyer ID from token and verify buyer is active/exists"""
    return buyer.id


class AuthIdentity:
    """Represents authenticated client: Chatbot service key, Buyer, or Internal User"""
    def __init__(
        self,
        auth_type: str,
        buyer: Optional[Buyer] = None,
        user_id: Optional[int] = None,
        role: Optional[str] = None,
        service_scopes: Optional[set[str]] = None,
    ):
        self.auth_type = auth_type  # "service", "buyer", "user"
        self.buyer = buyer
        self.user_id = user_id
        self.role = role
        self.service_scopes = service_scopes or set()

    @property
    def is_service(self) -> bool:
        return self.auth_type == "service"

    @property
    def is_buyer(self) -> bool:
        return self.auth_type == "buyer"

    @property
    def is_internal_user(self) -> bool:
        return self.auth_type == "user"

    def has_scope(self, scope: str) -> bool:
        return scope in self.service_scopes


_SERVICE_SCOPE_KEYS = {
    "service_api_key": {"orders:create", "payments:create"},
    "service_orders_read_key": {"orders:read"},
    "service_orders_cancel_key": {"orders:cancel"},
    "service_customers_key": {"customers:manage"},
    "service_refund_key": {"refunds:request"},
    "service_payment_status_key": {"payments:read"},
    "service_financial_summary_key": {"reports:summary"},
    "service_owner_numbers_key": {"owners:read"},
    "service_settlement_key": {"settlements:check"},
    "service_wa_verification_key": {"identity:verify"},
}


def _service_scopes_for_key(candidate: Optional[str]) -> set[str]:
    if not candidate:
        return set()
    matched = []
    for setting_name, configured_scopes in _SERVICE_SCOPE_KEYS.items():
        configured = getattr(settings, setting_name, "")
        if configured and hmac.compare_digest(candidate, configured):
            matched.append(configured_scopes)
    # Duplicated secrets fail closed instead of silently combining capabilities.
    return set(matched[0]) if len(matched) == 1 else set()


def require_service_scope(scope: str):
    async def check_service_scope(
        x_service_key: Optional[str] = Header(None, alias="X-Service-Key"),
        service_key: Optional[str] = Query(None, alias="service_key"),
    ) -> None:
        key = x_service_key or service_key
        scopes = _service_scopes_for_key(key)
        if not scopes:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid service key")
        if scope not in scopes:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Service key lacks required capability")
    return check_service_scope


async def get_auth_identity_optional_service_or_jwt(
    x_service_key: Optional[str] = Header(None, alias="X-Service-Key"),
    service_key: Optional[str] = Query(None, alias="service_key"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    db: AsyncSession = Depends(get_db)
) -> AuthIdentity:
    """
    Authenticate via either X-Service-Key / query service_key (Chatbot) or Bearer JWT (Buyer or Internal User).
    """
    # 1. Check Service Key
    key = x_service_key or service_key
    scopes = _service_scopes_for_key(key)
    if scopes:
        return AuthIdentity(auth_type="service", service_scopes=scopes)

    # 2. Check Bearer Token
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
        try:
            payload = decode_token(token)
            role = payload.get("role")
            sub_id = int(payload.get("sub"))

            if role == "buyer":
                from app.repositories import buyer_repo
                buyer = await buyer_repo.get_buyer_by_id(db, sub_id)
                if not buyer or not buyer.is_active:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Buyer account is inactive or not found"
                    )
                return AuthIdentity(auth_type="buyer", buyer=buyer, user_id=sub_id, role="buyer")
            else:
                user = await user_repo.get_user_by_id(db, sub_id)
                if user and not user.is_active:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="User is no longer active"
                    )
                jwt_role = payload.get("role_level")
                if user and user.role and user.role.level is not None:
                    effective_role = max(int(jwt_role) if jwt_role is not None else 3, user.role.level)
                else:
                    effective_role = jwt_role
                return AuthIdentity(auth_type="user", user_id=sub_id, role=effective_role)
        except HTTPException:
            raise
        except SQLAlchemyError as err:
            logger.error(f"Database error during authentication: {err}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Database error during authentication"
            )
        except Exception as e:
            logger.warning(f"Invalid auth token: {e}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authorization token"
            )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required (provide valid X-Service-Key or Bearer token)"
    )


async def get_current_user_role_level(
    payload: dict = Depends(get_current_user_payload),
    db: Optional[AsyncSession] = Depends(get_db),
) -> int:
    """
    Extract role level from token and verify against DB to prevent stale
    JWT role privilege escalation.
    - Buyers are strictly rejected from internal endpoints.
    - If user exists in DB, inactive users are rejected and demotions are enforced.
    - If DB lookup is unavailable or user unseeded in unit tests, uses token role_level.
    """
    if payload.get("role") == "buyer" or payload.get("role_level") == 0:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Buyers are not allowed to access internal APIs"
        )

    jwt_role_level = int(payload.get("role_level", 3))

    if db is not None:
        try:
            sub = payload.get("sub")
            if sub is not None:
                user = await user_repo.get_user_by_id(db, int(sub))
                if user:
                    if not user.is_active:
                        raise HTTPException(
                            status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="User is no longer active"
                        )
                    if user.role and user.role.level is not None:
                        # Enforce freshest DB role: lower level = higher privilege,
                        # so max() prevents maintaining higher privilege after demotion
                        return max(jwt_role_level, user.role.level)
        except HTTPException:
            raise
        except Exception:
            pass

    return jwt_role_level


async def require_wa_internal_key(
    x_service_key: Optional[str] = Header(None, alias="X-Service-Key"),
    x_internal_key: Optional[str] = Header(None, alias="X-Internal-Key")
) -> None:
    """Validate chatbot/internal-service identity. Accepts X-Service-Key or X-Internal-Key."""
    is_service_ok = "identity:verify" in _service_scopes_for_key(x_service_key)
    is_internal_ok = bool(
        x_internal_key
        and settings.chatbot_internal_key  # guard: reject if config is blank
        and hmac.compare_digest(x_internal_key, settings.chatbot_internal_key)
    )
    if not (is_service_ok or is_internal_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid service or internal key"
        )


def require_role(required_level: int):
    """Factory function to create role-based access control dependency"""
    async def check_role(
        role_level: int = Depends(get_current_user_role_level)
    ) -> int:
        # Lower numbers = higher privilege (1=Owner, 2=Admin, 3=Staff)
        if role_level > required_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Akses ditolak: Anda tidak memiliki izin untuk mengakses resource ini."
            )
        return role_level

    return check_role



# Convenience dependencies for specific roles
require_owner = require_role(1)  # Only Owner (level 1)
require_admin_or_owner = require_role(2)  # Admin (2) or Owner (1)
require_staff_or_above = require_role(3)  # Staff, Admin, or Owner (levels 1, 2, 3)
require_internal_user = require_staff_or_above  # All internal users (Staff, Admin, Owner)


async def require_order_read_access(
    auth: AuthIdentity = Depends(get_auth_identity_optional_service_or_jwt)
) -> AuthIdentity:
    """
    Allow access to read order by ID:
    - Internal users (Staff, Admin, Owner) via Bearer JWT
    - Service clients with 'orders:read' scope (SERVICE_ORDERS_READ_KEY via header or query param)
    """
    if auth.is_service:
        if not auth.has_scope("orders:read"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Service key lacks required capability"
            )
        return auth
    if auth.is_internal_user:
        role_level = int(auth.role) if auth.role is not None else 99
        if role_level > 3:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Akses ditolak: Anda tidak memiliki izin untuk mengakses resource ini."
            )
        return auth
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Akses ditolak: Anda tidak memiliki izin untuk mengakses resource ini."
    )


async def get_current_user(
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """Get currently authenticated User object with role details loaded"""
    user = await user_repo.get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    return user
