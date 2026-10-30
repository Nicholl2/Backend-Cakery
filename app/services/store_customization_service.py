import os
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.store_customization import StoreCustomization
from app.schemas.store_customization import (
    StoreCustomizationContentOut,
    StoreCustomizationLocaleOut,
    StoreCustomizationOut,
    StoreCustomizationPatch,
)
from app.utils.cloudinary_helper import upload_image_to_cloudinary

MAX_HERO_IMAGE_SIZE = 5 * 1024 * 1024
ALLOWED_HERO_IMAGE_EXTENSIONS = {".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png", ".webp": "webp"}


def _to_response(row: StoreCustomization) -> StoreCustomizationOut:
    return StoreCustomizationOut(
        content=StoreCustomizationContentOut(
            id=StoreCustomizationLocaleOut(
                hero_title=row.hero_title_id,
                hero_subtitle=row.hero_subtitle_id,
                about_title=row.about_title_id,
                about_description=row.about_description_id,
            ),
            en=StoreCustomizationLocaleOut(
                hero_title=row.hero_title_en,
                hero_subtitle=row.hero_subtitle_en,
                about_title=row.about_title_en,
                about_description=row.about_description_en,
            ),
        ),
        hero_image_url=row.hero_image_url,
        store_address=row.store_address,
        maps_embed_url=row.maps_embed_url,
        updated_at=row.updated_at.isoformat(),
    )


async def get_store_customization(db: AsyncSession) -> StoreCustomizationOut:
    row = await db.get(StoreCustomization, 1)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Konfigurasi homepage belum tersedia. Jalankan migrasi database terlebih dahulu.",
        )
    return _to_response(row)


async def update_store_customization(
    db: AsyncSession,
    data: StoreCustomizationPatch,
    updated_by: int,
) -> StoreCustomizationOut:
    changes = data.model_dump(exclude_unset=True)
    content_changes = changes.get("content")
    has_content_fields = bool(
        content_changes
        and any(fields for fields in content_changes.values() if fields is not None)
    )
    has_shared_fields = any(key != "content" for key in changes)
    if not has_content_fields and not has_shared_fields:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Minimal satu field konfigurasi homepage harus diisi.",
        )

    row = await db.get(StoreCustomization, 1, with_for_update=True)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Konfigurasi homepage belum tersedia. Jalankan migrasi database terlebih dahulu.",
        )

    content_changes = changes.pop("content", None)
    if content_changes:
        for locale, fields in content_changes.items():
            if fields is None:
                continue
            for field_name, value in fields.items():
                setattr(row, f"{field_name}_{locale}", value)

    for field_name, value in changes.items():
        setattr(row, field_name, value)
    row.updated_by = updated_by
    await db.commit()
    await db.refresh(row)
    return _to_response(row)


async def upload_hero_image(file: UploadFile) -> str:
    extension = os.path.splitext(file.filename or "")[1].lower()
    expected_format = ALLOWED_HERO_IMAGE_EXTENSIONS.get(extension)
    if expected_format is None or file.content_type != f"image/{expected_format}" and not (
        expected_format == "jpeg" and file.content_type == "image/jpeg"
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format banner harus JPEG, PNG, atau WEBP.",
        )

    if file.size is not None and file.size > MAX_HERO_IMAGE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Ukuran banner maksimal 5 MB.",
        )

    contents = await file.read(MAX_HERO_IMAGE_SIZE + 1)
    if len(contents) > MAX_HERO_IMAGE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Ukuran banner maksimal 5 MB.",
        )

    signatures = {
        "jpeg": contents.startswith(b"\xff\xd8\xff"),
        "png": contents.startswith(b"\x89PNG\r\n\x1a\n"),
        "webp": len(contents) >= 12 and contents.startswith(b"RIFF") and contents[8:12] == b"WEBP",
    }
    if not signatures[expected_format]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Isi file tidak cocok dengan format gambar yang diklaim.",
        )

    await file.seek(0)
    # Cloudinary derives the public ID from the filename. Use a unique name so
    # overwrite=True in the shared uploader can never replace an existing asset.
    file.filename = f"hero-{uuid4().hex}.{extension.lstrip('.')}"
    return await upload_image_to_cloudinary(
        file,
        folder="toti-cakery/store",
        max_size=MAX_HERO_IMAGE_SIZE,
    )
