import re
from fastapi import HTTPException, status

PHONE_INPUT_PATTERN = re.compile(r"^\+?[\d\s\-()]{7,25}$")


def normalize_phone(phone: str, as_http_exception: bool = True) -> str:
    """
    Normalize a phone number string into clean E.164 numeric format (without leading '+').
    
    Rules:
      1. Strip all non-digit characters (whitespace, dashes, plus signs, brackets).
      2. If phone starts with '620' -> replace with '62' (strip redundant zero after Indonesian code).
      3. If phone starts with '0' -> replace with '62' (default fallback for Indonesian national prefix).
      4. If phone already has international country code (e.g. 1..., 60..., 62..., 65..., 44...), keep as is.
      5. Validate length according to ITU-T E.164 standard (7 to 15 digits).
      
    Raises HTTPException(400) if as_http_exception=True, else ValueError.
    """
    if not phone or not isinstance(phone, str):
        msg = "Nomor telepon wajib diisi dan berupa string."
        if as_http_exception:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)
        raise ValueError(msg)
        
    digits = "".join(c for c in phone if c.isdigit())
    
    if digits.startswith("620"):
        digits = "62" + digits[3:]
    elif digits.startswith("0"):
        digits = "62" + digits[1:]
        
    if not (7 <= len(digits) <= 15):
        msg = f"Nomor telepon tidak valid: '{phone}'. Gunakan format E.164 (7-15 digit, contoh: 628123456789, 12025550123, 60123456789)."
        if as_http_exception:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)
        raise ValueError(msg)
        
    return digits


def validate_phone_e164(phone: str | None) -> str | None:
    """
    Validator helper for Pydantic v2 schemas.
    Accepts raw phone string and returns clean E.164 string or None.
    """
    if phone is None:
        return None
    phone_str = str(phone).strip()
    if not phone_str:
        return None
    return normalize_phone(phone_str, as_http_exception=False)
