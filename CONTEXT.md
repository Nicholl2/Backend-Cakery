# AI Context: Toti Cakery Backend

Dokumen ini berisi konteks teknis, arsitektur, pedoman pengkodean, integrasi pihak ketiga, serta keputusan desain (*architectural decisions*) terkini untuk backend Toti Cakery.

---

## 1. Tech Stack & Environment

- **Framework**: FastAPI (Python 3.12+)
- **Database ORM**: SQLAlchemy 2.0 (Full Asynchronous Mode via `asyncpg`)
- **Database Engine**: PostgreSQL (Hosted on Neon.tech / Cloud Postgres)
- **Validation & Serialization**: Pydantic v2 & `pydantic-settings`
- **Security & Token**: PyJWT / python-jose, bcrypt, passlib
- **HTTP Client**: `httpx.AsyncClient` (Asynchronous I/O)
- **Payment Gateway**: Midtrans Core API (Headless / Direct API Charge)
- **Chatbot & Live-Chat**: WA Service Integration (Bidirectional Webhooks)

---

## 2. Arsitektur & Pola Desain (Clean Layered Architecture)

Struktur kode backend wajib mematuhi pemisahan layer secara tegas:

```text
app/
├── models/         # Deklarasi entitas SQLAlchemy Base (Table Definition, Enums, Relationships)
├── schemas/        # Schema validasi Pydantic v2 (Request, Response, Out DTOs)
├── repositories/   # Layer manipulasi query database murni (select, insert, update, delete)
├── services/       # Layer logika bisnis, kalkulasi finansial, transaksi DB, & integrasi HTTP
├── api/
│   ├── dependencies.py # Dependency injection (Auth verification, RBAC, Database session)
│   └── routes/         # Router FastAPI (Endpoint definitions, HTTP status codes, query params)
└── core/
    ├── config.py       # Pydantic BaseSettings untuk pemetaan file .env
    ├── database.py     # Engine Async SQLAlchemy & session factory (get_db)
    ├── security.py     # Hashing password (bcrypt) & JWT token encode/decode
    └── migrations.py   # Skrip migrasi ringan otomatis untuk kolom dan indeks baru
```

---

## 3. Aturan Pengkodean Wajib (*Strict Rules*)

1. **Strict Async I/O**:
   - Seluruh operasi database wajib menggunakan `AsyncSession` dan di-`await`.
   - Seluruh komunikasi HTTP ke service eksternal (Midtrans, Chatbot) wajib menggunakan `httpx.AsyncClient`.
2. **Modern SQLAlchemy 2.0 Syntax**:
   - Wajib menggunakan `select()`, `update()`, `delete()`, `insert()` (Dilarang keras memakai sintaks lama seperti `db.query()`).
3. **Eager Loading Anti MissingGreenlet**:
   - Gunakan `selectinload()` atau `joinedload()` saat query relasi asinkronus untuk menghindari *Lazy-Load MissingGreenlet error*.
   - Saat objek telah di-`commit()`, jika respon endpoint memerlukan relasi nested, lakukan re-fetch dengan `selectinload()`.
4. **Optimistic Concurrency Control**:
   - Pengurangan dan pemulihan stok pada tabel `stock_items` wajib menyertakan filter kolom `version` dan increment `version = version + 1` untuk mencegah *race condition* / *overselling*.
5. **No Password Plaintext**:
   - Seluruh kata sandi wajib di-hash menggunakan algoritma `bcrypt`. Token verifikasi sekali pakai (`verify_token`) di-generate menggunakan `secrets.token_hex` atau UUID yang aman.
6. **International E.164 Phone Normalization**:
   - Seluruh input nomor telepon dan WhatsApp wajib melalui utilitas `app.utils.phone.normalize_phone` dan divalidasi via Pydantic validator (`validate_phone_e164`).
   - Format wajib berupa digit murni 7–15 digit (tanpa simbol `+`, spasi, atau tanda hubung). Prefix lokal `0...` otomatis dinormalisasi menjadi `62...`, sedangkan kode negara internasional (misal US `1...`, Malaysia `60...`) dipertahankan secara utuh.

---

## 4. Keamanan, Autentikasi & Otorisasi

Sistem menerapkan multi-channel authorization:
1. **Hierarchical RBAC Internal**:
   - **Level 1 (Owner)**: Akses mutlak (Laporan finansial P&L, manajemen user, penetapan harga jual).
   - **Level 2 (Admin)**: Manajemen operasional (Katalog produk, stok bahan, purchasing, input pengeluaran, update status order).
   - **Level 3 (Staff)**: Akses operasional dasar.
2. **Buyer Authentication**:
   - Pengguna Buyer Site menggunakan token JWT khusus ber-klaim `role: "buyer"`.
   - Mendukung login password dan verifikasi instan via nomor WhatsApp.
3. **Service-to-Service API Key**:
   - Endpoint khusus Chatbot Service diproteksi oleh pre-shared header `X-Service-Key` atau `X-Internal-Key`.
4. **CORS Configuration**:
   - `CORSMiddleware` menggunakan `allow_credentials=False` karena autentikasi berbasis HTTP Bearer Header (bukan cookie/session).
   - Default `cors_origins` mencakup `http://localhost:5173`, `http://127.0.0.1:5173`, dan domain frontend Vercel `https://toti-cakery.vercel.app`.
   - String `cors_origins` dibersihkan dari whitespace, tanda kutip, dan trailing slashes sebelum didaftarkan ke middleware.

---

## 5. Keputusan Desain & Perkembangan dari Rencana Awal (*Design Decisions*)

1. **WhatsApp Deep Link OTP (Zero SMS Cost) & Mock Mode**:
   - Verifikasi nomor telepon pembeli menggunakan alur WhatsApp Deep Link (`https://wa.me/...`) dengan kode `nonce` 6 digit yang dikonfirmasi langsung oleh bot WhatsApp.
   - Tersedia `WA_VERIFICATION_MODE=mock` untuk lingkungan development/staging agar tim frontend dapat melakukan pengujian registrasi/login instan tanpa bot WA.
   - Terdapat **Guardrail Keamanan Produksi**: jika `ENVIRONMENT=production`, sistem secara otomatis memaksakan mode `real` untuk mencegah bypass verifikasi.
2. **Pemisahan Chatbot Conversation Storage**:
   - Riwayat log percakapan teks disimpan secara lokal di Chatbot Service untuk efisiensi penyimpanan, sehingga tabel PostgreSQL fokus pada entitas transaksional (`customers`, `orders`, `invoices`, `payments`).
3. **Midtrans Headless Integration (Core API)**:
   - Backend tidak menggunakan pop-up Snap JS, melainkan memanggil Core API `/charge` secara langsung dari server untuk mendapatkan nomor Virtual Account BCA atau string QRIS dinamis, mempermudah integrasi baik di website maupun via teks WhatsApp.
4. **Sinkronisasi Otomatis HPP (Bill of Materials Cascading)**:
   - HPP produk dihitung otomatis dari komposisi bahan baku di tabel `recipes`. Penambahan/perubahan resep atau pembuatan PO baru secara otomatis memperbarui HPP seluruh produk terkait.
5. **Human Takeover Mechanism**:
   - Fitur takeover memfasilitasi eskalasi pesanan kue custom dari bot ke admin dengan batas waktu otomatis (`expires_at`), mencegah chatbot memotong percakapan live admin.