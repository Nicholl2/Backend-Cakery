"""
Utility sanitasi teks untuk mencegah XSS dan Script Injection.

Digunakan sebagai Pydantic field_validator di seluruh schema input.
"""
import re

# Pattern untuk mendeteksi tag HTML/script berbahaya
_DANGEROUS_PATTERNS = re.compile(
    r"<\s*script|<\s*/\s*script|javascript\s*:|on\w+\s*=|<\s*iframe|<\s*object|<\s*embed|<\s*form",
    re.IGNORECASE,
)

# Pattern username: alfanumerik, underscore, hyphen
USERNAME_PATTERN = r"^[a-zA-Z0-9_-]+$"


def sanitize_text(v: str) -> str:
    """
    Strip whitespace berlebih dan tolak input yang mengandung
    tag HTML/script berbahaya (XSS prevention).

    Raises ValueError jika ditemukan pola berbahaya.
    """
    if not isinstance(v, str):
        return v
    v = v.strip()
    if _DANGEROUS_PATTERNS.search(v):
        raise ValueError(
            "Input mengandung karakter atau script yang tidak diizinkan."
        )
    return v
