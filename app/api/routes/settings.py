from datetime import timezone

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from sqlalchemy import select
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

router = APIRouter(prefix="/settings", tags=["Settings"])


async def _get_sender(db: AsyncSession) -> EmailSenderSettingsOut:
    row = await db.get(EmailSenderSettings, 1)
    if row:
        updated = row.updated_at
        if updated and updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        return EmailSenderSettingsOut(
            from_email=row.from_email,
            from_name=row.from_name,
            updated_at=updated.astimezone(timezone.utc).isoformat() if updated else None,
        )
    return EmailSenderSettingsOut(from_email=app_settings.smtp_from_email,
                                  from_name=app_settings.smtp_from_name)


@router.get("/email-sender", response_model=EmailSenderSettingsOut,
            dependencies=[Depends(require_owner)], summary="Lihat identitas pengirim email toko")
async def get_email_sender_settings(db: AsyncSession = Depends(get_db)) -> EmailSenderSettingsOut:
    return await _get_sender(db)


@router.patch("/email-sender", response_model=EmailSenderSettingsOut,
             dependencies=[Depends(require_owner)], summary="Ubah nama/alamat pengirim email toko")
@limiter.limit("5/minute")
async def update_email_sender_settings(
    request: Request,
    data: EmailSenderSettingsUpdate,
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> EmailSenderSettingsOut:
    if not app_settings.smtp_host:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Pengiriman email belum dikonfigurasi.")
    row = await db.get(EmailSenderSettings, 1, with_for_update=True)
    if row is None:
        row = EmailSenderSettings(id=1, from_email=data.from_email,
                                  from_name=data.from_name, updated_by=user_id)
        db.add(row)
    else:
        row.from_email = data.from_email
        row.from_name = data.from_name
        row.updated_by = user_id
    await db.commit()
    await db.refresh(row)
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
    file: UploadFile = File(...),
) -> StoreHeroImageOut:
    secure_url = await store_customization_service.upload_hero_image(file)
    return StoreHeroImageOut(hero_image_url=secure_url)
