from fastapi import Header
from app.core.config import settings
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer
from fastapi.security.http import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.security import decode_token
from app.repositories import user_repo
from typing import Optional

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


async def get_current_user_role_level(
    payload: dict = Depends(get_current_user_payload)
) -> int:
    """Extract role level from token"""
    return int(payload.get("role_level", 3))

async def require_service_key(
    x_service_key: str = Header(None, alias="X-Service-Key")
) -> None:
    """Validate internal service key untuk chatbot / service-to-service calls."""
    if not x_service_key or x_service_key != settings.service_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid service key"
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
                detail=f"Insufficient permissions. Required role level: {required_level}, your level: {role_level}"
            )
        return role_level

    return check_role


# Convenience dependencies for specific roles
require_owner = require_role(1)  # Only Owner (level 1)
require_admin_or_owner = require_role(2)  # Admin (2) or Owner (1)
require_staff_or_above = require_role(3)  # Anyone (3, 2, 1)