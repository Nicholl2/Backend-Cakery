import os
import anyio
import cloudinary
import cloudinary.uploader
from fastapi import HTTPException, status, UploadFile
from app.core.config import settings

ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_AVATAR_FILE_SIZE = 5 * 1024 * 1024  # 5 MB


async def upload_image_to_cloudinary(
    file: UploadFile,
    folder: str = "toti-cakery/avatars",
    max_size: int = MAX_AVATAR_FILE_SIZE,
) -> str:
    """
    Validate and upload an image buffer directly to Cloudinary without saving to local disk.
    
    Returns:
        str: The HTTPS secure_url of the uploaded asset.
    """
    # 1. Validate file extension and MIME type
    ext = os.path.splitext(file.filename or "")[1].lower()
    content_type = file.content_type or ""

    if ext not in ALLOWED_IMAGE_EXTENSIONS or content_type not in ALLOWED_IMAGE_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Format file tidak didukung. Format yang diizinkan hanya image/jpeg, image/png, atau image/webp."
        )

    # 2. Validate file size (up to max_size, default 5MB)
    file_size = 0
    if hasattr(file, "size") and file.size is not None:
        file_size = file.size
    else:
        file.file.seek(0, os.SEEK_END)
        file_size = file.file.tell()
        file.file.seek(0)

    if file_size > max_size:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Ukuran file melebihi batas maksimal {max_size // (1024 * 1024)} MB."
        )

    # 3. Validate Cloudinary configuration
    if not (settings.cloudinary_cloud_name and settings.cloudinary_api_key and settings.cloudinary_api_secret):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Konfigurasi Cloudinary belum lengkap di server backend."
        )

    # 4. Configure Cloudinary SDK
    cloudinary.config(
        cloud_name=settings.cloudinary_cloud_name,
        api_key=settings.cloudinary_api_key,
        api_secret=settings.cloudinary_api_secret,
        secure=True
    )

    # 5. Upload stream directly from memory buffer
    try:
        def _sync_upload():
            file.file.seek(0)
            return cloudinary.uploader.upload(
                file.file,
                folder=folder,
                resource_type="image",
                overwrite=True,
            )

        upload_result = await anyio.to_thread.run_sync(_sync_upload)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Gagal mengunggah gambar ke Cloudinary: {str(e)}"
        )

    # 6. Retrieve secure_url
    secure_url = upload_result.get("secure_url") if isinstance(upload_result, dict) else None
    if not secure_url:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Gagal mendapatkan URL gambar aman (secure_url) dari Cloudinary."
        )

    return secure_url
