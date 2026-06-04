from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.schemas.auth import UserLogin, Token
from app.services import auth_service

router = APIRouter(
    prefix="/auth",
    tags=["Authentication"],
    responses={
        401: {"description": "Invalid credentials or unauthorized"},
        422: {"description": "Unprocessable entity - user inactive"}
    }
)


@router.post("/login", response_model=Token, status_code=status.HTTP_200_OK)
async def login(
    login_data: UserLogin,
    db: AsyncSession = Depends(get_db)
) -> Token:
    """
    Internal user login endpoint.
    
    Returns JWT token with user_id and role_level (1=Owner, 2=Admin, 3=Staff).
    
    - **username**: User's login username (3-50 characters)
    - **password**: User's password (minimum 6 characters)
    
    Returns:
    - **access_token**: JWT token for API authentication
    - **token_type**: Bearer token type
    - **user_id**: Authenticated user's ID
    - **role_level**: User's role level (1/2/3)
    - **username**: Authenticated username
    """
    return await auth_service.authenticate_user(db, login_data)