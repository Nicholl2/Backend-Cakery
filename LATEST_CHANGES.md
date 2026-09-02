# Ringkasan Perubahan Terbaru & Panduan Testing (CORS & Mock WA Verification)

Dokumen ini merangkum seluruh perubahan kode terbaru pada Backend Toti Cakery, penjelasan alur logika program, serta panduan langkah demi langkah untuk melakukan pengujian (*testing guide*).

---

## 📌 Daftar Perubahan Kode

### 1. `app/core/config.py`
- **Konfigurasi CORS Baru**: Default `cors_origins` diperbarui menyertakan domain frontend Vercel:
  ```python
  cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173,https://toti-cakery.vercel.app"
  ```
- **Pengaturan Environment & Mode Verifikasi**:
  ```python
  environment: str = "development"
  wa_verification_mode: str = "mock"  # Opsi: "mock" | "real"
  ```
- **Guardrail Keamanan Otomatis (Anti-Bypass di Server Produksi)**:
  Menggunakan `@model_validator(mode="after")` dan *property getter*:
  ```python
  @model_validator(mode="after")
  def enforce_production_security(self) -> "Settings":
      if self.environment.lower() == "production":
          self.wa_verification_mode = "real"
      return self
  ```
  > **Fungsi**: Jika `ENVIRONMENT="production"`, maka `WA_VERIFICATION_MODE` akan dipaksa bernilai `"real"`, sehingga fitur bypass/mock tidak dapat aktif di server produksi secara tidak sengaja.

---

### 2. `app/main.py`
- **Pembersihan String Origins CORS**:
  Memastikan setiap item origin di-strip dari spasi liar (*whitespace*), tanda kutip (`"` atau `'`), dan *trailing slash* (`/`):
  ```python
  origins = [
      origin.strip().strip("'\"").rstrip("/")
      for origin in settings.cors_origins.split(",")
      if origin.strip()
  ]

  app.add_middleware(
      CORSMiddleware,
      allow_origins=origins,
      allow_credentials=False,
      allow_methods=["*"],
      allow_headers=["*"],
  )
  ```
  > **Fungsi**: Mencegah kegagalan CORS karena perbedaan karakter seperti `https://toti-cakery.vercel.app/` (ada slash di ujung) atau spasi setelah koma pada file `.env`.

---

### 3. `app/schemas/auth.py`
- **Update Schema Response `WAVerifyStartResponse`**:
  ```python
  class WAVerifyStartResponse(BaseModel):
      nonce: str
      deeplink: str = ""
      expires_in: int
      verify_token: Optional[str] = None
      mock_mode: bool = False
  ```
  > **Fungsi**: Memungkinkan respons mengembalikan `verify_token` dan flag `mock_mode: True` saat mode mock aktif, sehingga frontend bisa langsung membaca token tanpa perlu menunggu webhook WA.

---

### 4. `app/services/buyer_auth_service.py`
- **Update `start_wa_verification(db, phone_number)`**:
  - **Jika `WA_VERIFICATION_MODE == "mock"`**:
    1. Normalisasi nomor telepon (format E.164, contoh `0812...` $\rightarrow$ `62812...`).
    2. Generate `nonce` 6 digit dan `verify_token` (UUID).
    3. Simpan record di tabel `otp_codes` langsung dengan `is_verified = True` dan `verify_token = <uuid>`.
    4. Return payload:
       ```json
       {
         "nonce": "AB12CD",
         "deeplink": "",
         "expires_in": 600,
         "verify_token": "ecbfd7a0-f70a-4045-8d11-597c485ab487",
         "mock_mode": true
       }
       ```
  - **Jika `WA_VERIFICATION_MODE == "real"`**:
    1. Memeriksa keberadaan `settings.chatbot_wa_number`.
    2. Membuat record OTP dengan `is_verified = False`.
    3. Menghasilkan deeplink WA asli (`https://wa.me/...`).
    4. Return payload dengan `mock_mode: false` dan `verify_token: null`.

- **Update `check_wa_verification_status(db, nonce)`**:
  - Jika `WA_VERIFICATION_MODE == "mock"`, langsung mengembalikan status `"verified"` dan `verify_token`.

---

## 🧪 Panduan Cara Ngetest (Testing Guide)

### Skenario 1: Testing Mock WA Verification Mode (Local / Staging)

#### 1. Pastikan Environment
Di file `.env` atau *environment variables*:
```env
ENVIRONMENT=development
WA_VERIFICATION_MODE=mock
```

#### 2. Test Step A: Memulai Verifikasi (Start)
Kirim request `POST /auth/verify/wa/start`:
```bash
curl -X POST "http://127.0.0.1:8000/auth/verify/wa/start" \
     -H "Content-Type: application/json" \
     -d '{"phone_number": "081234567890"}'
```
**Ekspektasi Output**:
```json
{
  "nonce": "A1B2C3",
  "deeplink": "",
  "expires_in": 600,
  "verify_token": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "mock_mode": true
}
```

#### 3. Test Step B: Polling Status (Opsional)
Jika frontend tetap menjalankan polling:
```bash
curl -X GET "http://127.0.0.1:8000/auth/verify/wa/status?nonce=A1B2C3"
```
**Ekspektasi Output**:
```json
{
  "status": "verified",
  "verify_token": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
}
```

#### 4. Test Step C: Eksekusi Registrasi Buyer dengan `verify_token`
Gunakan `verify_token` yang didapat dari langkah di atas:
```bash
curl -X POST "http://127.0.0.1:8000/auth/buyer/register" \
     -H "Content-Type: application/json" \
     -d '{
       "name": "Testing User",
       "email": "testing@example.com",
       "phone": "081234567890",
       "password": "password123",
       "verify_token": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
     }'
```
**Ekspektasi Output**: HTTP 200/201 dengan Access Token JWT pembeli (`role: "buyer"`).

---

### Skenario 2: Testing Production Guardrail (Keamanan)

Untuk memastikan mode mock **TIDAK BISA** aktif di server produksi:

Jalankan perintah pengujian cepat dengan python:
```bash
python3 -c "
from app.core.config import Settings

# Simulasi pengaturan environment production
prod_settings = Settings(
    database_url='sqlite+aiosqlite:///:memory:',
    environment='production',
    wa_verification_mode='mock'  # Mencoba set mock di prod
)

print('Environment:', prod_settings.ENVIRONMENT)
print('WA Verification Mode:', prod_settings.WA_VERIFICATION_MODE)
assert prod_settings.WA_VERIFICATION_MODE == 'real', 'Guardrail Gagal!'
print('✅ Guardrail Sukses: Mode dipaksa menjadi REAL!')
"
```

---

### Skenario 3: Testing CORS Headers

Uji pre-flight OPTIONS request dari domain frontend Vercel:
```bash
curl -I -X OPTIONS "http://127.0.0.1:8000/products/" \
     -H "Origin: https://toti-cakery.vercel.app" \
     -H "Access-Control-Request-Method: GET"
```
**Ekspektasi Header**:
```text
access-control-allow-origin: https://toti-cakery.vercel.app
access-control-allow-methods: *
```
