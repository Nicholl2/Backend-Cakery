from app.utils.phone import normalize_phone
print(normalize_phone("08123456789", as_http_exception=False))
print(normalize_phone("+628987654321", as_http_exception=False))
print(normalize_phone("08111111111", as_http_exception=False))
