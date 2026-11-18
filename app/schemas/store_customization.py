import re
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.config import settings

MAX_HERO_IMAGES = 5
MAX_HERO_IMAGE_URL_LENGTH = 2048


class _StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _plain_text(value: str | None, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} harus berupa teks.")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} tidak boleh kosong; kirim null untuk memakai teks bawaan.")
    if "<" in normalized or ">" in normalized:
        raise ValueError(f"{field_name} hanya menerima teks biasa, bukan HTML.")
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", normalized):
        raise ValueError(f"{field_name} mengandung karakter kontrol yang tidak diizinkan.")
    return normalized


def _validate_cloudinary_image(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > MAX_HERO_IMAGE_URL_LENGTH:
        raise ValueError("hero_image_url tidak valid.")
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme != "https"
        or parsed.hostname != "res.cloudinary.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or ".." in parsed.path.split("/")
    ):
        raise ValueError("Gambar banner harus berupa URL HTTPS Cloudinary yang valid.")
    cloud_name = settings.cloudinary_cloud_name.strip()
    if cloud_name and not parsed.path.startswith(f"/{cloud_name}/image/upload/"):
        raise ValueError("URL gambar harus berasal dari akun Cloudinary toko.")
    return value.strip()


def _validate_hero_images(value: list[str] | None) -> list[str] | None:
    """Validate every carousel entry and keep the first occurrence of each URL."""
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError("hero_image_urls harus berupa daftar URL gambar.")
    if len(value) > MAX_HERO_IMAGES:
        raise ValueError(f"Maksimal {MAX_HERO_IMAGES} gambar banner.")

    urls: list[str] = []
    for item in value:
        validated = _validate_cloudinary_image(item)
        if validated is None:
            raise ValueError("Setiap item hero_image_urls harus berupa URL gambar.")
        if validated not in urls:
            urls.append(validated)
    return urls


def _validate_google_maps_embed(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("maps_embed_url tidak valid.")
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme != "https"
        or parsed.hostname != "www.google.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.path != "/maps/embed"
        or not parsed.query
        or parsed.fragment
    ):
        raise ValueError("Peta harus memakai URL embed HTTPS Google Maps.")
    return value.strip()


class StoreCustomizationLocalePatch(_StrictSchema):
    hero_title: str | None = Field(None, max_length=120)
    hero_subtitle: str | None = Field(None, max_length=600)
    about_title: str | None = Field(None, max_length=120)
    about_description: str | None = Field(None, max_length=3000)

    @field_validator("hero_title", "hero_subtitle", "about_title", "about_description", mode="before")
    @classmethod
    def validate_text(cls, value, info):
        return _plain_text(value, field_name=info.field_name)


class StoreCustomizationContentPatch(_StrictSchema):
    id: StoreCustomizationLocalePatch | None = None
    en: StoreCustomizationLocalePatch | None = None


class StoreCustomizationPatch(_StrictSchema):
    content: StoreCustomizationContentPatch | None = None
    hero_image_url: str | None = Field(None, max_length=MAX_HERO_IMAGE_URL_LENGTH)
    hero_image_urls: list[str] | None = Field(None, max_length=MAX_HERO_IMAGES)
    store_address: str | None = Field(None, max_length=500)
    maps_embed_url: str | None = Field(None, max_length=2048)

    @field_validator("hero_image_url", mode="before")
    @classmethod
    def validate_hero_image(cls, value):
        return _validate_cloudinary_image(value)

    @field_validator("hero_image_urls", mode="before")
    @classmethod
    def validate_hero_image_list(cls, value):
        return _validate_hero_images(value)

    @model_validator(mode="after")
    def reject_ambiguous_hero_images(self):
        # Presence, not truthiness: an explicit null is still a choice.
        if "hero_image_url" in self.model_fields_set and "hero_image_urls" in self.model_fields_set:
            raise ValueError("Kirim hero_image_urls atau hero_image_url, bukan keduanya.")
        return self

    @field_validator("maps_embed_url", mode="before")
    @classmethod
    def validate_map(cls, value):
        return _validate_google_maps_embed(value)

    @field_validator("store_address", mode="before")
    @classmethod
    def validate_address(cls, value):
        return _plain_text(value, field_name="store_address")


class StoreCustomizationLocaleOut(BaseModel):
    hero_title: str | None
    hero_subtitle: str | None
    about_title: str | None
    about_description: str | None


class StoreCustomizationContentOut(BaseModel):
    id: StoreCustomizationLocaleOut
    en: StoreCustomizationLocaleOut


class StoreCustomizationOut(BaseModel):
    content: StoreCustomizationContentOut
    hero_image_url: str | None
    hero_image_urls: list[str] = Field(default_factory=list)
    store_address: str | None
    maps_embed_url: str | None
    updated_at: str


class StoreHeroImageOut(BaseModel):
    hero_image_url: str
