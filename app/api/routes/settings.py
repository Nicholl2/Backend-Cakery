import logging
from datetime import timezone

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user_id, require_owner
from app.core.config import settings as app_settings
from app.core.database import get_db
from app.core.rate_limiter import limiter
from app.models.email_sender_settings import EmailSenderSettings
from app.schemas.email_settings import EmailSenderSettingsOut, EmailSenderSettingsUpdate
from app.schemas.store_customization import (
    StoreCustomizationOut,
    StoreCustomizationPatch,
    StoreHeroImageOut,
)
from app.services import store_customization_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["Settings"])


async def _get_sender(db: AsyncSession) -> EmailSenderSettingsOut:
    row = await db.get(EmailSenderSettings, 1)
    updated = row.updated_at if row else None
    if updated and updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return EmailSenderSettingsOut(
        from_email=(row.from_email if row and row.from_email else app_settings.smtp_from_email),
        from_name=(row.from_name if row and row.from_name else app_settings.smtp_from_name),
        updated_at=updated.astimezone(timezone.utc).isoformat() if updated else None,
    )


async def _persist_sender(
    db: AsyncSession,
    data: EmailSenderSettingsUpdate,
    user_id: int,
) -> None:
    """Create or update the singleton row, tolerating concurrent first writes."""
    row = await db.get(EmailSenderSettings, 1, with_for_update=True)
    if row is None:
        row = EmailSenderSettings(
            id=1,
            from_email=data.from_email,
            from_name=data.from_name,
            updated_by=user_id,
        )
        db.add(row)
    else:
        row.from_email = data.from_email
        row.from_name = data.from_name
        row.updated_by = user_id

    try:
        await db.commit()
    except IntegrityError:
        # Another owner request inserted the singleton row first. Roll back the
        # failed insert and update the winning row instead of returning a 500.
        await db.rollback()
        row = await db.get(EmailSenderSettings, 1, with_for_update=True)
        if row is None:
            raise
        row.from_email = data.from_email
        row.from_name = data.from_name
        row.updated_by = user_id
        await db.commit()

    await db.refresh(row)


@router.get("/email-sender", response_model=EmailSenderSettingsOut,
            dependencies=[Depends(require_owner)], summary="Lihat identitas pengirim email toko")
async def get_email_sender_settings(db: AsyncSession = Depends(get_db)) -> EmailSenderSettingsOut:
    return await _get_sender(db)


@router.patch("/email-sender", response_model=EmailSenderSettingsOut,
             dependencies=[Depends(require_owner)], summary="Ubah nama/alamat pengirim email toko")
@limiter.limit("5/minute")
async def update_email_sender_settings(
    request: Request,
    response: Response,
    data: EmailSenderSettingsUpdate,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> EmailSenderSettingsOut:
    if not app_settings.smtp_host:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Pengiriman email belum dikonfigurasi.")
    await _persist_sender(db, data, user_id)
    logger.info(
        "Email sender settings updated by user_id=%s (from=%s)",
        user_id,
        data.from_email,
    )
    return await _get_sender(db)


@router.patch(
    "/store-customization",
    response_model=StoreCustomizationOut,
    dependencies=[Depends(require_owner)],
    summary="Ubah konten homepage toko (Owner only)",
)
@limiter.limit("5/minute")
async def update_store_customization_settings(
    request: Request,
    response: Response,
    data: StoreCustomizationPatch,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> StoreCustomizationOut:
    return await store_customization_service.update_store_customization(
        db,
        data,
        updated_by=user_id,
    )


@router.post(
    "/store-customization/hero-image",
    response_model=StoreHeroImageOut,
    dependencies=[Depends(require_owner)],
    summary="Unggah banner homepage ke Cloudinary (Owner only)",
)
@limiter.limit("5/minute")
async def upload_store_customization_hero_image(
    request: Request,
    response: Response,
    file: UploadFile = File(...),
) -> StoreHeroImageOut:
    secure_url = await store_customization_service.upload_hero_image(file)
    return StoreHeroImageOut(hero_image_url=secure_url)
