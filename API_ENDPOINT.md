# Toti Cakery API Endpoints Specification

Dokumentasi lengkap seluruh endpoint REST API Backend Toti Cakery (FastAPI).

---

## 1. Aturan Keamanan & Autentikasi

| Jenis Klien / Aktor | Mekanisme Autentikasi | Header / Dependency | Keterangan |
| :--- | :--- | :--- | :--- |
| **Owner (Level 1)** | JWT Bearer Token | `Authorization: Bearer <token>` (`require_owner`) | Akses penuh: manajemen user, financial report, pricing |
| **Admin (Level 2)** | JWT Bearer Token | `Authorization: Bearer <token>` (`require_admin_or_owner`) | Akses operasional: CRUD produk, stock, purchasing, expenses, update order |
| **Staff (Level 3)** | JWT Bearer Token | `Authorization: Bearer <token>` (`require_staff_or_above`) | Akses dasar internal |
| **Buyer Site (Pelanggan)** | JWT Bearer Token | `Authorization: Bearer <token>` (`get_current_buyer_id`) | Akses akun buyer, buat review, transaksi web |
| **Chatbot & Internal Services** | Pre-shared Service Key | `X-Service-Key: <key>` / `X-Internal-Key: <key>` | Komunikasi headless: webhook, order placement, takeover |
| **Public Webhook Midtrans** | SHA512 Signature Hash | Signature di payload webhook | Verifikasi integritas pembayaran Midtrans tanpa token |
| **Public Endpoint** | Tanpa Autentikasi | - | Katalog produk, FAQ, cek status ulasan |

---

## 2. Daftar Endpoint per Modul

### A. Autentikasi & Verifikasi Akun (`/auth`)

| Method | Endpoint | Auth / Permission | Deskripsi |
| :--- | :--- | :--- | :--- |
| `POST` | `/auth/bootstrap` | Public (First-time only) | Inisialisasi akun Owner pertama jika database masih kosong |
| `POST` | `/auth/login` | Public | Login akun internal (Owner/Admin/Staff), return JWT token & role level |
| `POST` | `/auth/verify/wa/start` | Public | Memulai sesi verifikasi WA (E.164 7-15 digit). Di mode real: return `nonce` & `deeplink`. Di mode mock (`WA_VERIFICATION_MODE=mock` non-prod): otomatis verifikasi dan return `verify_token` & `mock_mode: true` |
| `POST` | `/auth/verify/wa/confirm` | `X-Service-Key` / `X-Internal-Key` | Konfirmasi nomor pengirim oleh chatbot saat customer mengirim pesan verifikasi |
| `GET` | `/auth/verify/wa/status` | Public (Polling) | Cek status verifikasi WA berdasarkan `nonce`. Di mode mock/verified: return status `"verified"` & `verify_token` |
| `POST` | `/auth/buyer/register` | Public (`verify_token`) | Registrasi akun pembeli baru menggunakan token verifikasi WA dan nomor telepon valid |
| `POST` | `/auth/buyer/login` | Public | Login pembeli via email/password atau phone/`verify_token` |
| `POST` | `/auth/buyer/login-phone` | Public | Login pembeli via nomor telepon (E.164 internasional) dan password |
| `POST` | `/auth/buyer/login/otp` | Public (`verify_token`) | Login pembeli via nomor telepon dan token verifikasi WA |
| `POST` | `/auth/buyer/reset-password` | Public (`verify_token`) | Reset password pembeli menggunakan token verifikasi |
| `POST` | `/auth/seller/forgot-password/request` | Public | Request OTP reset password untuk akun internal/seller |
| `POST` | `/auth/seller/forgot-password/verify` | Public | Verifikasi kode OTP seller, return `verify_token` |
| `POST` | `/auth/seller/reset-password` | Public (`verify_token`) | Reset password akun seller menggunakan token verifikasi |

---

### B. Produk & Katalog (`/products`)

| Method | Endpoint | Auth / Permission | Deskripsi |
| :--- | :--- | :--- | :--- |
| `POST` | `/products/` | Admin / Owner | Buat master produk baru (HPP default 0 sebelum resep diisi) |
| `GET` | `/products/` | Public | List katalog produk (Filter: `only_active`, `kategori`) |
| `GET` | `/products/{product_id}` | Public | Detail produk lengkap beserta ketersediaan stok bahan (`is_available`) |
| `PUT` | `/products/{product_id}` | Admin / Owner | Update data produk (nama, deskripsi, kategori, is_active, slug, minimum_order, dll.) |
| `DELETE` | `/products/{product_id}` | Admin / Owner | Hapus produk beserta seluruh relasi resep dan riwayat harganya |
| `POST` | `/products/{product_id}/image` | Admin / Owner | Upload gambar produk langsung ke Cloudinary (`toti-cakery/products`, maks 5MB, format JPEG/PNG/WEBP), simpan HTTPS secure_url ke DB |
| `PATCH` | `/products/{product_id}/price` | Admin / Owner | Tetapkan/ubah harga jual produk (audit riwayat harga otomatis) |
| `GET` | `/products/{product_id}/pricing` | Public / Internal | Lihat rincian breakdown HPP bahan + kalkulasi margin vs harga jual |
| `GET` | `/products/{product_id}/price-history`| Public / Internal | Riwayat perubahan harga jual produk oleh Owner |

---

### C. Resep / Bill of Materials (BOM) (`/recipes`)

*Base URL: `/recipes/{product_id}/recipes`*

| Method | Endpoint | Auth / Permission | Deskripsi |
| :--- | :--- | :--- | :--- |
| `GET` | `/recipes/{product_id}/recipes/` | Public / Internal | Lihat seluruh komposisi bahan + total HPP produk |
| `POST` | `/recipes/{product_id}/recipes/` | Internal | Tambah bahan baku ke resep (otomatis sinkronisasi HPP produk) |
| `PUT` | `/recipes/{product_id}/recipes/{recipe_id}` | Internal | Ubah takaran bahan pada resep (otomatis re-kalkulasi HPP) |
| `DELETE` | `/recipes/{product_id}/recipes/{recipe_id}` | Internal | Hapus bahan dari resep (otomatis re-kalkulasi HPP) |

---

### D. Manajemen Stok Bahan Baku (`/stock`)

| Method | Endpoint | Auth / Permission | Deskripsi |
| :--- | :--- | :--- | :--- |
| `POST` | `/stock/` | Internal | Tambah bahan baku atau kemasan baru |
| `GET` | `/stock/` | Internal | List semua item stok (Filter: `bahan_baku` / `kemasan`) |
| `GET` | `/stock/{stock_id}` | Internal | Detail item stok (stok tersedia, harga per satuan, supplier, alert min stok) |
| `PUT` | `/stock/{stock_id}` | Internal | Update data bahan baku / kemasan |
| `DELETE` | `/stock/{stock_id}` | Internal | Hapus bahan (dicegah jika masih digunakan pada resep produk) |

---

### E. Purchasing & Supplier (`/purchases`)

| Method | Endpoint | Auth / Permission | Deskripsi |
| :--- | :--- | :--- | :--- |
| `POST` | `/purchases/suppliers` | Internal | Tambah master data supplier baru |
| `GET` | `/purchases/suppliers` | Internal | List semua supplier (Filter: `only_active`) |
| `GET` | `/purchases/suppliers/{supplier_id}` | Internal | Detail supplier |
| `PUT` | `/purchases/suppliers/{supplier_id}` | Internal | Update data supplier |
| `DELETE` | `/purchases/suppliers/{supplier_id}` | Internal | Hapus supplier (gagal jika ada Purchase Order terkait) |
| `POST` | `/purchases/purchases` | Authenticated User | Buat Purchase Order (PO) baru beserta item bahan yang dibeli |
| `GET` | `/purchases/purchases` | Internal | List PO (Filter: `only_received`, `supplier_id`) |
| `GET` | `/purchases/purchases/{purchase_id}` | Internal | Detail PO beserta daftar item pemesanan |
| `PUT` | `/purchases/purchases/{purchase_id}` | Internal | Update status PO (misal: tandai sudah diterima, tanggal diterima) |
| `DELETE` | `/purchases/purchases/{purchase_id}` | Internal | Hapus PO (dicegah jika PO sudah berstatus diterima) |

---

### F. Pelanggan & Human Takeover (`/customers` & `/admin`)

| Method | Endpoint | Auth / Permission | Deskripsi |
| :--- | :--- | :--- | :--- |
| `GET` | `/customers` | `X-Service-Key` | Ambil data pelanggan berdasarkan query `?nomor_wa=...` |
| `POST` | `/customers` | `X-Service-Key` | UPSERT data profil pelanggan dari interaksi WhatsApp |
| `POST` | `/customers/{nomor_wa}/takeover` | `X-Service-Key` | Aktifkan/nonaktifkan status human takeover dan set waktu kedaluwarsa |
| `GET` | `/customers/{nomor_wa}/takeover` | `X-Service-Key` | Cek status aktif dan masa berlaku takeover sebelum chatbot merespons |
| `GET` | `/admin/takeover-handlers` | `X-Service-Key` | Ambil daftar nomor WA admin yang bertugas menangani live takeover |

---

### G. Pemesanan / Orders (`/orders`)

| Method | Endpoint | Auth / Permission | Deskripsi |
| :--- | :--- | :--- | :--- |
| `POST` | `/orders/buyer` | Buyer JWT (`get_current_buyer`) | Buat order baru khusus Buyer (otomatis derive `customer_id` dari identitas JWT, reservasi stok bahan, generate invoice) |
| `GET` | `/orders/buyer` | Buyer JWT (`get_current_buyer`) | Ambil seluruh riwayat pesanan milik Buyer yang sedang login |
| `GET` | `/orders/buyer/{id}` | Buyer JWT (`get_current_buyer`) | Detail pesanan spesifik milik Buyer (isolasi data aman antarpembeli) |
| `POST` | `/orders` | `X-Service-Key` | Buat order baru via chatbot (reservasi stok bahan via Optimistic Locking, generate invoice) |
| `GET` | `/orders/latest` | `X-Service-Key` | Ambil order terbaru pelanggan berdasarkan query `?nomor_wa=...` |
| `POST` | `/orders/{order_id}/cancel` | `X-Service-Key` | Pembatalan otomatis (hanya jika invoice `unpaid`, stok bahan dikembalikan) |
| `PATCH` | `/orders/{order_id}/status` | Admin / Owner | Update status pesanan (`pending`, `in_process`, `ready`, `delivered`, `picked_up`, `cancelled`). Menembak push webhook saat `ready`. |

---

### H. Pembayaran Midtrans Core API (`/payments`)

| Method | Endpoint | Auth / Permission | Deskripsi |
| :--- | :--- | :--- | :--- |
| `POST` | `/payments` | `X-Service-Key` / Buyer JWT | Charge pembayaran ke Midtrans (Bank Transfer BCA VA atau QRIS), validasi nominal anti-tampering dan verifikasi kepemilikan order untuk Buyer |
| `GET` | `/payments/{order_id}/status` | `X-Service-Key` / Buyer JWT | Cek status tagihan, total terbayar, sisa tagihan, dan refresh transaksi pending untuk Chatbot & Buyer |
| `POST` | `/payments/notify` | Public Webhook | Listener webhook otomatis Midtrans (validasi signature SHA512, auto-settlement invoice & order) |

---

### I. Biaya Operasional / Expenses (`/expenses`)

| Method | Endpoint | Auth / Permission | Deskripsi |
| :--- | :--- | :--- | :--- |
| `POST` | `/expenses` | Admin / Owner | Catat pengeluaran operasional baru (gaji, listrik, sewa, dsb.) |
| `GET` | `/expenses` | Authenticated User | List riwayat pengeluaran (Filter: `kategori`, rentang tanggal, paginasi) |
| `GET` | `/expenses/summary/dashboard` | Authenticated User | Ringkasan total dan breakdown biaya untuk dashboard P&L |
| `GET` | `/expenses/{expense_id}` | Authenticated User | Detail single data pengeluaran |

---

### J. Laporan & Analitik (`/reports`)

| Method | Endpoint | Auth / Permission | Deskripsi |
| :--- | :--- | :--- | :--- |
| `GET` | `/reports/summary` | `X-Service-Key` | Ringkasan finansial (Revenue, Expenses, Order Count, AOV, Top 5 Products) untuk bot |
| `GET` | `/reports/financial` | Owner Only | Laporan komprehensif Laba/Rugi (P&L), Gross Profit, Net Profit |
| `GET` | `/reports/analytics` | Owner Only | Analitik penjualan bulanan, tren produk terlaris, dan distribusi rating |

---

### K. Ulasan Produk / Reviews (`/reviews`)

| Method | Endpoint | Auth / Permission | Deskripsi |
| :--- | :--- | :--- | :--- |
| `POST` | `/reviews/` | Buyer Auth | Buat ulasan produk (rating 1-5 dan komentar) |
| `GET` | `/reviews/product/{product_id}` | Public | List semua ulasan untuk produk tertentu |
| `GET` | `/reviews/{review_id}` | Public | Detail ulasan |
| `PUT` | `/reviews/{review_id}` | Buyer Author | Edit ulasan milik sendiri |
| `DELETE` | `/reviews/{review_id}` | Buyer Author / Admin / Owner | Hapus ulasan |

---

### L. Tanya Jawab / FAQ Management (`/faq`)

| Method | Endpoint | Auth / Permission | Deskripsi |
| :--- | :--- | :--- | :--- |
| `POST` | `/faq` | Admin / Owner | Tambah item pertanyaan & jawaban FAQ baru |
| `GET` | `/faq` | Public | List FAQ (Filter: `only_active`, paginasi) |
| `GET` | `/faq/{faq_id}` | Public | Detail FAQ |
| `PUT` | `/faq/{faq_id}` | Admin / Owner | Update pertanyaan/jawaban FAQ |
| `DELETE` | `/faq/{faq_id}` | Admin / Owner | Hapus item FAQ |

---

### M. Manajemen Pengguna Internal (`/users`)

| Method | Endpoint | Auth / Permission | Deskripsi |
| :--- | :--- | :--- | :--- |
| `POST` | `/users` | Owner Only | Daftarkan akun internal baru (Owner, Admin, atau Staff) |
| `PATCH` | `/users/{user_id}/takeover-handler` | Owner Only | Set status apakah admin tersebut bertugas menangani live takeover |