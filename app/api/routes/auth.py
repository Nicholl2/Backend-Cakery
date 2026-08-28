from fastapi import APIRouter, Depends, status, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.api.dependencies import require_wa_internal_key
from app.schemas.auth import (
    UserLogin, Token,
    OTPSendRequest, OTPSendResponse,
    OTPVerifyRequest, OTPVerifyResponse,
    BuyerRegisterRequest, BuyerAuthResponse,
    BuyerLoginRequest, BuyerLoginPhoneRequest, BuyerLoginOTPRequest,
    BuyerResetPasswordRequest, SellerForgotPasswordRequest,
    SellerForgotPasswordVerifyRequest, SellerResetPasswordRequest,
    WAVerifyStartRequest, WAVerifyStartResponse,
    WAVerifyConfirmRequest, WAVerifyStatusResponse
)
from app.schemas.user import UserBootstrap, UserOut
from app.services import auth_service, buyer_auth_service, user_service

router = APIRouter(
    tags=["Authentication"],
    responses={
        401: {"description": "Invalid credentials or unauthorized"},
        422: {"description": "Unprocessable entity - user inactive"}
    }
)


@router.post("/bootstrap", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def bootstrap(
    data: UserBootstrap,
    db: AsyncSession = Depends(get_db)
) -> UserOut:
    """
    Bootstrap the first Owner user in the system if the users table is empty.
    """
    user = await user_service.bootstrap_owner(db, data)
    return UserOut.model_validate(user)


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


# ── WA DEEP LINK OTP ENDPOINTS ──────────────────────────────────────────────

@router.post("/verify/wa/start", response_model=WAVerifyStartResponse, status_code=status.HTTP_201_CREATED)
async def verify_wa_start(
    data: WAVerifyStartRequest,
    db: AsyncSession = Depends(get_db)
):
    """Start WhatsApp Deep Link OTP verification flow"""
    return await buyer_auth_service.start_wa_verification(db, data.phone_number)


@router.post("/verify/wa/confirm", status_code=status.HTTP_200_OK, dependencies=[Depends(require_wa_internal_key)])
async def verify_wa_confirm(
    data: WAVerifyConfirmRequest,
    db: AsyncSession = Depends(get_db)
):
    """Confirm verification of phone number by chatbot (requires service or internal key)"""
    return await buyer_auth_service.confirm_wa_verification(db, data.nonce, data.sender_phone)


@router.get("/verify/wa/status", response_model=WAVerifyStatusResponse, status_code=status.HTTP_200_OK)
async def verify_wa_status(
    nonce: str,
    db: AsyncSession = Depends(get_db)
):
    """Check WhatsApp verification status by nonce (polling)"""
    return await buyer_auth_service.check_wa_verification_status(db, nonce)


# ── BUYER AUTHENTICATION ENDPOINTS ──────────────────────────────────────────

@router.post("/buyer/otp/send", response_model=OTPSendResponse, status_code=status.HTTP_200_OK)
async def buyer_otp_send(
    data: OTPSendRequest,
    db: AsyncSession = Depends(get_db)
):
    """Send OTP code (mock '7777' for development) to email or phone number"""
    return await buyer_auth_service.send_otp(db, data.target, data.channel.value, data.purpose.value)


@router.post("/buyer/otp/verify", response_model=OTPVerifyResponse, status_code=status.HTTP_200_OK)
async def buyer_otp_verify(
    data: OTPVerifyRequest,
    db: AsyncSession = Depends(get_db)
):
    """Verify buyer OTP code (mock '7777') and get a single-use verification token"""
    return await buyer_auth_service.verify_otp(db, data.otp_id, data.code)


@router.post("/buyer/register", response_model=BuyerAuthResponse, status_code=status.HTTP_201_CREATED)
async def buyer_register(
    data: BuyerRegisterRequest,
    db: AsyncSession = Depends(get_db)
):
    """Register a new buyer account using verified token"""
    phone = data.phone_number or data.phone
    if not phone:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="phone or phone_number is required"
        )
    return await buyer_auth_service.register_buyer(
        db=db,
        name=data.name,
        email=data.email,
        phone=phone,
        password=data.password,
        verify_token=data.verify_token
    )


@router.post("/buyer/login", response_model=BuyerAuthResponse, status_code=status.HTTP_200_OK)
async def buyer_login(
    data: BuyerLoginRequest,
    db: AsyncSession = Depends(get_db)
):
    """Login buyer using email and password, or phone and verify_token"""
    if data.verify_token:
        phone = data.phone_number or data.phone
        if not phone:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="phone_number is required for OTP login"
            )
        return await buyer_auth_service.login_buyer_otp(db, phone, data.verify_token)
    else:
        if not data.email or not data.password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="email and password are required for password login"
            )
        return await buyer_auth_service.login_buyer_password(db, data.email, data.password)


@router.post("/buyer/login-phone", response_model=BuyerAuthResponse, status_code=status.HTTP_200_OK)
async def buyer_login_phone(
    data: BuyerLoginPhoneRequest,
    db: AsyncSession = Depends(get_db)
):
    """Login buyer using phone number and password"""
    return await buyer_auth_service.login_buyer_phone(db, data.phone_number, data.password)


@router.post("/buyer/login/otp", response_model=BuyerAuthResponse, status_code=status.HTTP_200_OK)
async def buyer_login_otp(
    data: BuyerLoginOTPRequest,
    db: AsyncSession = Depends(get_db)
):
    """Login buyer using phone number and verified token"""
    phone = data.phone_number or data.phone
    if not phone:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="phone or phone_number is required"
        )
    return await buyer_auth_service.login_buyer_otp(db, phone, data.verify_token)


@router.post("/buyer/reset-password", status_code=status.HTTP_200_OK)
async def buyer_reset_password(
    data: BuyerResetPasswordRequest,
    db: AsyncSession = Depends(get_db)
):
    """Reset buyer password using verified token"""
    return await buyer_auth_service.reset_buyer_password(db, data.verify_token, data.new_password)


# ── SELLER AUTHENTICATION ENDPOINTS ──────────────────────────────────────────

@router.post("/seller/forgot-password/request", response_model=OTPSendResponse, status_code=status.HTTP_200_OK)
async def seller_forgot_password_request(
    data: SellerForgotPasswordRequest,
    db: AsyncSession = Depends(get_db)
):
    """Request password reset OTP for seller account (lookup user by email)"""
    return await buyer_auth_service.request_seller_forgot_password(db, data.email)


@router.post("/seller/forgot-password/verify", response_model=OTPVerifyResponse, status_code=status.HTTP_200_OK)
async def seller_forgot_password_verify(
    data: SellerForgotPasswordVerifyRequest,
    db: AsyncSession = Depends(get_db)
):
    """Verify OTP code for seller password reset and get a single-use verification token"""
    return await buyer_auth_service.verify_seller_forgot_password(db, data.otp_id, data.code)


@router.post("/seller/reset-password", status_code=status.HTTP_200_OK)
async def seller_reset_password(
    data: SellerResetPasswordRequest,
    db: AsyncSession = Depends(get_db)
):
    """Reset seller password using verified token"""
    return await buyer_auth_service.reset_seller_password(db, data.verify_token, data.new_password)