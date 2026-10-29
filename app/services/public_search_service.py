from sqlalchemy.ext.asyncio import AsyncSession

from app.models.faq_item import FaqItem
from app.models.product import Product
from app.repositories import public_search_repo
from app.schemas.search import (
    PublicFaqSearchResult,
    PublicProductSearchResult,
    PublicSearchResponse,
    PublicStoreInfoSearchResult,
)

RESULTS_PER_TYPE = 5
STORE_INFO_KEYWORDS = {"toti", "toko", "kontak", "whatsapp", "wa", "hubungi"}


async def search_public_content(db: AsyncSession, query: str) -> PublicSearchResponse:
    products, faqs = await _search_database_content(db, query)
    tokens = set(query.casefold().split())
    store_info = []
    if tokens & STORE_INFO_KEYWORDS:
        store_info.append(
            PublicStoreInfoSearchResult(
                key="contact",
                title="Kontak Toti Cakery",
                description="Lihat nomor WhatsApp resmi toko untuk menghubungi Toti Cakery.",
                action="fetch_contact_endpoint",
            )
        )

    product_results = [
        PublicProductSearchResult(
            id=product.id,
            nama_produk=product.nama_produk,
            slug=product.slug,
            deskripsi=product.deskripsi,
            kategori=product.kategori,
            harga_jual=product.harga_jual,
            image_url=product.image_url,
        )
        for product in products
    ]
    faq_results = [
        PublicFaqSearchResult(
            id=faq.id,
            pertanyaan=faq.pertanyaan,
            jawaban=faq.jawaban,
        )
        for faq in faqs
    ]

    return PublicSearchResponse(
        query=query,
        products=product_results,
        faqs=faq_results,
        store_info=store_info,
        total=len(product_results) + len(faq_results) + len(store_info),
    )


async def _search_database_content(
    db: AsyncSession,
    query: str,
) -> tuple[list[Product], list[FaqItem]]:
    # A fixed cap per result type bounds query and response work for anonymous users.
    products = await public_search_repo.search_products(db, query, RESULTS_PER_TYPE)
    faqs = await public_search_repo.search_active_faqs(db, query, RESULTS_PER_TYPE)
    return products, faqs
