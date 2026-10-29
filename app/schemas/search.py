from decimal import Decimal
from typing import Literal

from pydantic import BaseModel


class PublicProductSearchResult(BaseModel):
    id: int
    nama_produk: str
    slug: str | None = None
    deskripsi: str | None = None
    kategori: str | None = None
    harga_jual: Decimal | None = None
    image_url: str | None = None


class PublicFaqSearchResult(BaseModel):
    id: int
    pertanyaan: str
    jawaban: str


class PublicStoreInfoSearchResult(BaseModel):
    key: Literal["contact"]
    title: str
    description: str
    action: Literal["fetch_contact_endpoint"]


class PublicSearchResponse(BaseModel):
    query: str
    products: list[PublicProductSearchResult]
    faqs: list[PublicFaqSearchResult]
    store_info: list[PublicStoreInfoSearchResult]
    total: int
