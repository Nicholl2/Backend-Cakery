import pytest
from pydantic import ValidationError

from app.schemas.faq import FaqCreate, FaqUpdate


def test_faq_create_accepts_nullable_english_content():
    faq = FaqCreate(
        pertanyaan="Bagaimana pesan kue?",
        jawaban="Hubungi kami untuk melakukan pemesanan.",
        pertanyaan_en="How can I order a cake?",
        jawaban_en="Contact us to place an order.",
    )

    assert faq.pertanyaan_en == "How can I order a cake?"
    assert faq.jawaban_en == "Contact us to place an order."
    assert FaqCreate(
        pertanyaan="Bagaimana pesan kue?",
        jawaban="Hubungi kami untuk melakukan pemesanan.",
    ).pertanyaan_en is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("pertanyaan_en", "   "),
        ("pertanyaan_en", "tiny"),
        ("pertanyaan_en", "q" * 501),
        ("jawaban_en", "short"),
        ("jawaban_en", "a" * 2001),
        ("jawaban_en", "answer\x00 text"),
    ],
)
def test_faq_english_fields_validate_content(field, value):
    with pytest.raises(ValidationError):
        FaqCreate(
            pertanyaan="Bagaimana pesan kue?",
            jawaban="Hubungi kami untuk melakukan pemesanan.",
            **{field: value},
        )


def test_faq_update_distinguishes_omitted_english_from_explicit_null():
    omitted = FaqUpdate(pertanyaan="Apakah bisa dikirim?")
    cleared = FaqUpdate(jawaban_en=None)

    assert omitted.model_dump(exclude_unset=True) == {"pertanyaan": "Apakah bisa dikirim?"}
    assert cleared.model_dump(exclude_unset=True) == {"jawaban_en": None}
