# 📝 Ringkasan Pembaruan Keamanan, Otorisasi, & Optimasi Backend (Toti Cakery)

Dokumen ini merangkum pembaruan arsitektur keamanan, otorisasi endpoint, sanitasi data, dan optimasi performa query database pada backend FastAPI Toti Cakery. Dokumen ini ditujukan sebagai panduan teknis dan referensi integrasi bagi tim **Frontend (Web/Mobile)** dan **Chatbot Service**.

---

## 1. Pembaruan Otorisasi & Hak Akses Endpoint (Access Control)

Untuk menjamin keamanan data bisnis dan kepatuhan hak akses, seluruh endpoint mutasi kini mewajibkan otorisasi yang sesuai:

### A. Manajemen Resep (`/products/{product_id}/recipes`)
- **`GET /{product_id}/recipes/`**: Memerlukan autentikasi internal (`Bearer JWT` Seller/Admin/Owner).
- **`POST /`, `PUT /{recipe_id}`, `DELETE /{recipe_id}`**: Memerlukan role **Admin** (Level 2) atau **Owner** (Level 1).

### B. Manajemen Stok Bahan & Kemasan (`/stock`)
- **`GET /` & `GET /{stock_id}`**: Memerlukan autentikasi internal (`Bearer JWT`).
- **`POST /`, `PUT /{stock_id}`, `DELETE /{stock_id}`**: Memerlukan role **Admin** atau **Owner**.

### C. Pengadaan / Purchasing (`/purchasing`)
- **`GET /suppliers`, `GET /purchases`, `GET /purchases/{id}`**: Memerlukan autentikasi internal (`Bearer JWT`).
- **`POST /suppliers`, `PUT /suppliers/{id}`, `DELETE /suppliers/{id}`**: Memerlukan role **Admin** atau **Owner**.
- **`POST /purchases`**: Memerlukan autentikasi internal (`Bearer JWT` Staff/Admin/Owner).
- **`PUT /purchases/{id}`, `DELETE /purchases/{id}`**: Memerlukan role **Admin** atau **Owner**.

### D. Rekomendasi Harga Jual (`/pricing`)
- **`GET /pricing/product/{product_id}`**: Memerlukan autentikasi internal (`Bearer JWT`).

---

## 2. Penguatan Autentikasi & Anti-Abuse (Rate Limiting & OTP)

### A. Proteksi Anti-Brute Force pada OTP
- Verifikasi kode OTP (`POST /auth/verify/otp` dan alur forgot password) dibatasi maksimal **5 kali percobaan salah**.
- Jika salah memasukkan kode sebanyak 5 kali berturut-turut, record OTP otomatis dihanguskan dan server mengembalikan status `429 Too Many Requests`. Pengguna wajib meminta kode OTP baru.

### B. Proteksi Anti-Email Enumeration
- Endpoint reset password seller (`POST /auth/seller/forgot-password/request`) dan buyer (`POST /auth/buyer/forgot-password`) selalu mengembalikan status `200 OK` terlepas dari apakah email terdaftar atau tidak untuk mencegah pemetaan akun oleh pihak luar.

### C. Penerapan Rate Limiting
Endpoint autentikasi dan reset password berikut kini dilengkapi pembatasan laju request (*Rate Limiting*):
- `POST /auth/login` (Max 5 req/menit)
- `POST /auth/buyer/register` (Max 5 req/menit)
- `POST /auth/buyer/login` (Max 5 req/menit)
- `POST /auth/buyer/login-phone` (Max 5 req/menit)
- `POST /auth/buyer/login/otp` (Max 5 req/menit)
- `POST /auth/buyer/reset-password` (Max 3 req/menit)
- `POST /auth/buyer/forgot-password` (Max 3 req/menit)
- `POST /auth/buyer/reset-password/email` (Max 3 req/menit)
- `POST /auth/seller/forgot-password/request` (Max 3 req/menit)
- `POST /auth/seller/forgot-password/verify` (Max 3 req/menit)
- `POST /auth/seller/reset-password` (Max 3 req/menit)

---

## 3. Optimasi Database & Query Performance

### A. Asynchronous Pricing Queries
- Query kalkulasi modal resep produk pada `pricing_repo` direfaktor menjadi asinkron murni menggunakan SQLAlchemy 2.0 async select, menghilangkan potensi *thread blocking* pada event loop server.

### B. Agregasi Pengeluaran & Laporan (P&L Analytics)
- Perhitungan total pengeluaran dan rekapitulasi kategori pengeluaran kini dieksekusi langsung di level engine PostgreSQL (`func.sum` dan `GROUP BY`), mengurangi waktu respons dan konsumsi RAM backend secara signifikan.

### C. Pagination Riwayat Pesanan Customer & Ulasan
- Query riwayat pesanan (`get_orders_by_customer_id`) dan daftar ulasan global (`get_all`) kini mendukung parameter `limit` dan `offset` untuk mencegah *unbounded queries*.

### D. Optimasi FAQ Count
- Perhitungan total FAQ menggunakan query agregasi SQL `func.count(FaqItem.id)` langsung di database.

---

## 4. Hardening Error Messages & Sanitasi Data

- **Penyederhanaan Pesan Error 403:** Pesan error otorisasi diseragamkan menjadi pesan umum tanpa mengekspos angka level hierarki peran internal (`role_level`).
- **Penyembunyian Detail Eksepsi Cloudinary:** Error unggah media dicatat di internal server log dan mengembalikan pesan yang aman dan ramah pengguna ke client HTTP.
- **Sensor OTP pada Mode Mock:** Log aplikasi pada mode mock SMTP tidak lagi menampilkan kode OTP secara telanjang (*masked logging*).

---

## 5. Konfigurasi Lingkungan Produksi (Production Safety)

- **CORS Production Allowlist:** Pada environment `production`, domain `localhost` dan `127.0.0.1` otomatis disaring dan dihilangkan dari CORS allowlist demi mencegah eksploitasi lintas origin dari browser lokal.
- **Fail-Fast Secret Validation:** Server akan menolak *startup* di mode `production` jika variabel kunci rahasia (`SECRET_KEY` atau `SERVICE_API_KEY`) masih menggunakan nilai bawaan (*default placeholder*).
