# Ringkasan Perubahan Terbaru & Panduan Testing

Dokumen ini merangkum seluruh perubahan kode terbaru pada Backend Toti Cakery, penjelasan alur logika program, serta panduan langkah demi langkah untuk melakukan pengujian (*testing guide*).

---

## 📌 Daftar Perubahan Kode Terbaru

### 0. User & Buyer Avatar Upload via Cloudinary Direct Integration
- **Direct Memory Stream Helper (`app/utils/cloudinary_helper.py`)**:
  - Menyediakan helper `upload_image_to_cloudinary(file, folder="toti-cakery/avatars")` yang men-stream buffer memory berkas langsung ke Cloudinary API tanpa menyentuh disk lokal atau repositori Git.
  - Memvalidasi MIME type berkas: hanya mengizinkan `image/jpeg`, `image/png`, dan `image/webp`.
  - Memvalidasi ukuran berkas: membatasi maksimal 5 MB (menolak berkas lebih besar dengan HTTP 400).
  - Menangani error upload Cloudinary dengan HTTP 502 Bad Gateway dan error konfigurasi dengan HTTP 500 Internal Server Error.
- **Model & Database Persistence (`app/models/buyer.py`, `app/models/user.py`, `app/core/migrations.py`)**:
  - Menambahkan kolom `avatar_url` (String 500, nullable=True) ke model `Buyer` dan `User`.
  - Menambahkan migration otomatis di `ensure_buyer_columns` dan `ensure_user_columns` pada PostgreSQL database.
- **Repositories (`app/repositories/buyer_repo.py`, `app/repositories/user_repo.py`)**:
  - Menambahkan method `update_avatar_url` pada `buyer_repo` dan `user_repo` untuk menyimpan `secure_url` Cloudinary ke database.
- **Services (`app/services/buyer_auth_service.py`, `app/services/user_service.py`)**:
  - Menambahkan `upload_buyer_avatar` di `buyer_auth_service.py`.
  - Menambahkan `upload_user_avatar` di `user_service.py`.
  - Memasukkan `avatar_url` pada seluruh respon autentikasi buyer (`register_buyer`, `login_buyer_password`, `login_buyer_phone`, `login_buyer_otp`).
- **Schemas (`app/schemas/auth.py`, `app/schemas/user.py`)**:
  - Menambahkan `avatar_url` ke `BuyerAuthResponse`, `UserResponse`, dan `UserOut`.
  - Menambahkan schema `BuyerProfileResponse`.
- **API Endpoints (`app/api/routes/customer.py`, `app/api/routes/user.py`, `app/main.py`)**:
  - `POST /buyers/me/avatar` (dan alias `/v1/buyers/me/avatar`, `/api/v1/buyers/me/avatar`): Upload avatar Buyer yang sedang login.
  - `GET /buyers/me` (dan alias `/v1/buyers/me`): Mengambil profil akun Buyer yang sedang login.
  - `POST /users/me/avatar`: Upload avatar internal User (Owner/Admin/Staff) yang sedang login.
- **Automated Tests (`app/test_avatar_upload.py`)**:
  - Pengujian komprehensif validasi MIME type, validasi ukuran >5MB, mocking Cloudinary upload, persistensi DB SQLite, dan endpoint API Buyer/User.

---
- **1. Root Path & Proxy Fix (`app/main.py`)**:
  - Menambahkan parameter `root_path="/api"` pada instansiasi `FastAPI(..., root_path="/api")` agar redirect internal FastAPI, dokumentasi OpenAPI/Swagger (`/docs`), dan reverse proxy HTTPS (seperti Vercel rewrite `/api/*`) diarahkan dengan akurat tanpa memicu mixed content atau salah prefix rute.
  - Memasang auto-seed pada event `lifespan` startup: sistem mendeteksi apakah tabel `roles` masih kosong; jika ya, seeder master data awal otomatis dijalankan.

- **2. Master Data Database Seeder (`app/core/seeder.py` & `app/seed_data.py`)**:
  - Dibuat modul seeder idempoten yang mengisi data awal:
    - **Roles**: `Owner` (Level 1), `Admin` (Level 2), `Staff`/`Seller` (Level 3), `Buyer` (Level 4).
    - **Akun Internal Default**:
      - Superadmin/Owner: `imeng` / `Admin_123` (Email: `imeng@toticakery.com`, HP/WA: `08111111111`)
      - Admin: `ameng` / `Admin_123` (Email: `ameng@toticakery.com`, HP/WA: `08222222222`)
      - Staff/Seller: `smeng` / `Staff_123` (Email: `smeng@toticakery.com`, HP/WA: `08333333333`)
    - **Akun Buyer Default**:
      - `aceng@gmail.com` / `Aceng_123` (HP: `08123456789`)
    - **Dukungan Multi-Identifier Login Internal (`/auth/login`)**:
      - Kolom `email` dan `phone_number` ditambahkan ke model `User` (`app/models/user.py`).
      - Schema `UserLogin` menerima `identifier` fleksibel (`username`, `email`, atau `phone` / `phone_number`).
      - Service `authenticate_user` otomatis mencari `User` berdasarkan `username` -> `email` -> `phone_number`, sehingga pengguna admin/owner/staff di Frontend dapat login menggunakan email, username, nomor HP, maupun WA secara langsung.
    - **Master Suppliers**: `PT Sukses Bahan Kue` & `CV Kemasan Cantik`.
    - **Master Stock Items**: Tepung Terigu, Gula Pasir, Mentega Wisman, Telur Ayam, Box Kue Eksklusif.
    - **Master Products & Recipes**: Lapis Legit Premium, Chiffon Cake Pandan, Brownies Fudgy Almond (terkoneksi dengan takaran resep `Recipe` ke stok bahan baku & kemasan).
    - **Master FAQ Items**: 3 item FAQ mengenai daya tahan kue, pembayaran Midtrans, dan metode pengiriman.
  - Skrip CLI mandiri: `python -m app.seed_data` atau `python app/seed_data.py`.

- **3. Endpoint Pesanan & Pembayaran Khusus Buyer JWT (`/orders` & `/payments`)**:
  - **Dependencies (`app/api/dependencies.py`)**:
    - `get_current_buyer`: Memvalidasi token JWT Buyer dan mengembalikan model `Buyer` yang aktif.
    - `get_auth_identity_optional_service_or_jwt`: Mendukung autentikasi fleksibel `X-Service-Key` (Chatbot) ATAU `Authorization: Bearer <token>` (Buyer / Internal User).
  - **Order Endpoints (`app/api/routes/order.py`)**:
    - `POST /orders/buyer`: Membuat pesanan khusus Buyer. `customer_id` didapatkan langsung dari token JWT (nomor HP Buyer yang sedang login).
    - `GET /orders/buyer`: Mengambil seluruh riwayat pesanan milik Buyer yang sedang login.
    - `GET /orders/buyer/{id}`: Mengambil detail spesifik pesanan milik Buyer yang sedang login (dengan isolasi data aman 404 jika bukan pemilik).
    - Mempertahankan `POST /orders`, `GET /orders/latest`, dan `POST /orders/{order_id}/cancel` untuk Chatbot (`require_service_key`).
  - **Payment Endpoints (`app/api/routes/payment.py`)**:
    - `POST /payments`: Dapat diakses oleh Chatbot (`X-Service-Key`) maupun Buyer JWT (`Authorization: Bearer <token>`). Jika diakses oleh Buyer JWT, sistem memvalidasi kepemilikan pesanan sebelum membuat transaksi Midtrans.
    - `GET /payments/{order_id}/status`: Dapat diakses oleh Chatbot maupun Buyer JWT pemilik pesanan untuk memantau status pembayaran.

---

### 1. Refactoring Upload Gambar Produk (Cloudinary Integration — Fix Vercel Read-Only Filesystem)
- **Latar Belakang & Masalah**:
  - Runtime serverless Vercel memiliki sistem berkas *read-only* (`Read-only file system (os error 30)`).
  - Penulisan berkas statis lokal ke `/static/products/` menyebabkan HTTP 500 saat di-deploy di Vercel.
- **Solusi & Implementasi**:
  1. **Direct Memory Stream Upload**:
     - Memanfaatkan `UploadFile.file` (memory stream) langsung ke API Cloudinary tanpa pernah menulis ke filesystem lokal.
     - Dijalankan secara non-blocking via `anyio.to_thread.run_sync` agar event loop FastAPI tetap responsif.
  2. **Konfigurasi Environment Cloudinary (`app/core/config.py` & `.env.example`)**:
     - `CLOUDINARY_CLOUD_NAME`
     - `CLOUDINARY_API_KEY`
     - `CLOUDINARY_API_SECRET`
  3. **Folder Penyimpanan Terstruktur**:
     - Folder default Cloudinary: `toti-cakery/products`.
     - URL publik HTTPS (`secure_url`) disimpan ke kolom `products.image_url`.
  4. **Dependencies**:
     - Ditambahkan `cloudinary==1.46.2` ke `requirements.txt`.

---

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

### 5. `app/utils/phone.py` & DTO Schemas (Normalisasi Nomor Telepon Internasional E.164)
- **Helper `normalize_phone(phone: str)`**:
  - Membersihkan seluruh karakter non-digit (spasi, tanda `+`, tanda hubung `-`, kurung `()`).
  - **Fallback Default Indonesia**: Jika diawali prefix `0`, otomatis diubah menjadi `62` (contoh: `08123456789` $\rightarrow$ `628123456789`).
  - **Pembersihan Redundant Zero**: Jika diawali `620`, diubah menjadi `62` (contoh: `+62 0812...` $\rightarrow$ `62812...`).
  - **Dukungan Internasional**: Jika diawali kode negara internasional lainnya (misal `1...` US, `60...` Malaysia, `65...` Singapura, `44...` UK, `81...` Jepang, dsb.), kode negara tetap dipertahankan.
  - **Validasi Standar E.164**: Memastikan panjang digit bersih berada pada rentang **7 hingga 15 digit**.
- **Pydantic Schemas Validator (`validate_phone_e164`)**:
  - Diterapkan pada seluruh DTO/Schema terkait (`app/schemas/auth.py`, `app/schemas/customer.py`, `app/schemas/user.py`) agar otomatis menolak format tidak valid atau membersihkan input menjadi format E.164 sebelum masuk ke database.

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

---

### Skenario 4: Testing Normalisasi Nomor Telepon Internasional (E.164)

Uji berbagai variasi format nomor telepon melalui Python unit runner:
```bash
python3 -c "
from app.utils.phone import normalize_phone

# 1. Indonesia local prefix 0 -> 62
assert normalize_phone('0812-3456-7890') == '6281234567890'
assert normalize_phone('+62 0812 3456 7890') == '6281234567890'

# 2. International numbers (US, Malaysia, Singapore, UK, Japan)
assert normalize_phone('+1 (202) 555-0123') == '12025550123'
assert normalize_phone('+60 12-345 6789') == '60123456789'
assert normalize_phone('+65 9123 4567') == '6591234567'
assert normalize_phone('+44 7911 123456') == '447911123456'

print('✅ Seluruh pengujian normalisasi nomor telepon internasional SUKSES!')
"
```

---

### Skenario 5: Testing Upload Foto Produk ke Cloudinary

#### 1. Konfigurasi Environment di `.env`
```env
CLOUDINARY_CLOUD_NAME=your-cloud-name
CLOUDINARY_API_KEY=your-api-key
CLOUDINARY_API_SECRET=your-api-secret
```

#### 2. Test Eksekusi via cURL (Admin / Owner Token)
```bash
curl -X POST "http://127.0.0.1:8000/products/1/image" \
     -H "Authorization: Bearer <ADMIN_OR_OWNER_JWT_TOKEN>" \
     -F "file=@/path/to/sample_cake.jpg"
```

**Ekspektasi Output**:
```json
{
  "id": 1,
  "nama_produk": "Kue Cokelat Lumer",
  "deskripsi": "Kue lezat premium",
  "kategori": "Cakes",
  "hpp_total": 45000.0,
  "harga_jual": 65000.0,
  "markup_percentage": 0.4444,
  "is_active": true,
  "is_available": true,
  "image_url": "https://res.cloudinary.com/your-cloud-name/image/upload/v1234567890/toti-cakery/products/sample.jpg",
  "slug": "kue-cokelat-lumer",
  "rating": 5.0,
  "review_count": 1,
  "sold_count": 10,
  "is_featured": true,
  "minimum_order": 1,
  "parent_category": "Cakes",
  "recipes": [],
  "created_at": "2026-09-05T10:00:00Z",
  "updated_at": "2026-09-05T12:00:00Z"
}
```
> URL gambar kini berupa HTTPS publik Cloudinary yang dapat diakses langsung oleh browser tanpa ketergantungan pada disk serverless lokal.


