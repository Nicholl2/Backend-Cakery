# Toti Cakery API Endpoints Specification

Dokumentasi lengkap seluruh endpoint REST API Backend Toti Cakery (FastAPI).

---

## 1. Aturan Keamanan & Autentikasi
### A. Mekanisme Akses & Token

| Jenis Klien / Aktor | Mekanisme Autentikasi | Header / Dependency | Keterangan |
| :--- | :--- | :--- | :--- |
| **Owner (Level 1)** | JWT Bearer Token | `Authorization: Bearer <token>` (`require_owner`) | Akses penuh: manajemen user, financial report, pricing |
| **Admin (Level 2)** | JWT Bearer Token | `Authorization: Bearer <token>` (`require_admin_or_owner`) | Akses operasional: CRUD produk, stock, purchasing, expenses, update order |
| **Staff (Level 3)** | JWT Bearer Token | `Authorization: Bearer <token>` (`require_staff_or_above`) | Akses dasar internal |
| **Buyer Site (Pelanggan)** | JWT Bearer Token | `Authorization: Bearer <token>` (`get_current_buyer_id`) | Akses akun buyer, buat review, transaksi web |
| **Chatbot & Internal Services** | Pre-shared Service Key | `X-Service-Key: <key>` / `X-Internal-Key: <key>` | Komunikasi headless: webhook, order placement, takeover |
| **Public Webhook Midtrans** | SHA512 Signature Hash | Signature di payload webhook | Verifikasi integritas pembayaran Midtrans tanpa token |
| **Public Endpoint** | Tanpa Autentikasi | - | Katalog produk, FAQ, cek status ulasan |

### B. Validasi Input & Pencegahan XSS

Seluruh endpoint menerapkan perlindungan ketat (Hardening) pada level skema payload:
- **Phone Number / WhatsApp**: Hanya menerima karakter angka. Panjang 10-16 karakter. Seluruh nomor otomatis diubah ke format internasional **E.164** (`628...`).
- **Username**: Maksimal 25 karakter. Hanya boleh berisi huruf, angka, `_`, dan `-` (regex: `^[a-zA-Z0-9_-]+$`).
- **Email**: Format email standar, maksimal 100 karakter.
- **Teks Bebas (Nama, Alamat, Notes, Review)**: Maksimal karakter ketat diberlakukan (contoh: notes/alamat maks 500 karakter, komentar review maks 1000). 
- **Pencegahan XSS / Script Injection**: Seluruh field berupa string/teks akan menolak string yang mengandung tag berbahaya (seperti `<script>`, `javascript:`, `<iframe>`, `<object>`, `<form>`). API akan langsung mengembalikan HTTP 422 Unprocessable Entity atau HTTP 400 Bad Request jika mendeteksi payload berbahaya.

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

### F. Pelanggan & Profil Pembeli (`/customers`, `/buyers`, `/admin`)

| Method | Endpoint | Auth / Permission | Deskripsi |
| :--- | :--- | :--- | :--- |
| `GET` | `/customers` | `X-Service-Key` | Ambil data pelanggan berdasarkan query `?nomor_wa=...` |
| `POST` | `/customers` | `X-Service-Key` | UPSERT data profil pelanggan dari interaksi WhatsApp |
| `POST` | `/customers/{nomor_wa}/takeover` | `X-Service-Key` | Aktifkan/nonaktifkan status human takeover dan set waktu kedaluwarsa |
| `GET` | `/customers/{nomor_wa}/takeover` | `X-Service-Key` | Cek status aktif dan masa berlaku takeover sebelum chatbot merespons |
| `GET` | `/admin/takeover-handlers` | `X-Service-Key` | Ambil daftar nomor WA admin yang bertugas menangani live takeover |
| `GET` | `/buyers/me` (alias: `/v1/buyers/me`) | Buyer JWT (`get_current_buyer`) | Ambil data profil Buyer yang sedang login beserta `avatar_url` |
| `POST` | `/buyers/me/avatar` (alias: `/v1/buyers/me/avatar`) | Buyer JWT (`get_current_buyer`) | Upload foto avatar akun Buyer langsung di-stream ke Cloudinary (`toti-cakery/avatars/`, maks 5MB, format JPEG/PNG/WEBP), simpan `secure_url` ke database |


---

### G. Pemesanan / Orders (`/orders`)

| Method | Endpoint | Auth / Permission | Deskripsi |
| :--- | :--- | :--- | :--- |
| `GET` | `/orders` | Staff / Admin / Owner | List seluruh pesanan toko untuk Seller (Staff/Admin/Owner) dengan relasi lengkap (Customer, OrderItems, Invoice, Payments, amount_paid, amount_due). Filter: `status`, `limit`, `offset`. |
| `GET` | `/orders/{order_id}` | Staff / Admin / Owner | Detail pesanan spesifik untuk Seller beserta customer, item kustom/produk (dengan field `product_name`), invoice, dan ringkasan pembayaran. |
| `POST` | `/orders/custom` | Staff / Admin / Owner | Buat pesanan kustom buatan seller tanpa master produk (otomatis create customer, bypass stock deduction, generate invoice, set `created_via = 'seller'`). |
| `POST` | `/orders/buyer` | Buyer JWT (`get_current_buyer`) | Buat order baru khusus Buyer (otomatis derive `customer_id` dari identitas JWT, reservasi stok bahan via Optimistic Locking, generate invoice). |
| `GET` | `/orders/buyer` | Buyer JWT (`get_current_buyer`) | Ambil seluruh riwayat pesanan milik Buyer yang sedang login. |
| `GET` | `/orders/buyer/{id}` | Buyer JWT (`get_current_buyer`) | Detail pesanan spesifik milik Buyer (isolasi data aman antarpembeli). |
| `POST` | `/orders` | `X-Service-Key` | Buat order baru via chatbot (reservasi stok bahan via Optimistic Locking, generate invoice). |
| `GET` | `/orders/latest` | `X-Service-Key` | Ambil order terbaru pelanggan berdasarkan query `?nomor_wa=...` |
| `POST` | `/orders/{order_id}/cancel` | `X-Service-Key` | Pembatalan otomatis oleh pelanggan (hanya jika invoice `unpaid`, stok bahan dikembalikan). |
| `PATCH` | `/orders/{order_id}/status` | Staff / Admin / Owner | Update status pesanan (`pending`, `in_process`, `ready`, `delivered`, `picked_up`, `cancelled`, `refunded`). Memvalidasi aturan transisi State Machine (termasuk transisi `cancelled` -> `refunded` untuk menyelesaikan refund manual). Memicu webhook notifikasi `/ready` jika status menjadi `ready`, atau sinyal 2.b webhook `/refunded` jika status menjadi `refunded` (SETELAH `db.commit()`). |
| `POST` | `/orders/{order_id}/refund` | Staff/Admin/Owner OR `X-Service-Key` | Memproses pembatalan sekaligus refund untuk pesanan berstatus DP/Lunas. Memicu API Midtrans, rollback stok bahan, dan trigger webhook `/refunded` ke Chatbot SETELAH database commit. Jika dipanggil Chatbot (`X-Service-Key`), memvalidasi kepemilikan `nomor_wa` dan status wajib `pending`. Mengembalikan payload DTO dengan penanda `refund_mode` (`auto` jika direct API Midtrans berhasil dengan status akhir order `refunded`, atau `manual` jika metode VA/QRIS 412 yang mewajibkan transfer manual dengan status order `cancelled`). Jika dipanggil oleh Admin/Seller, pesanan langsung bertransisi menjadi `refunded` dan memicu sinyal 2.b webhook `/refunded`. |

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
| `GET` | `/users/me` | Authenticated User | Ambil profil lengkap user internal/seller yang sedang login (termasuk field `role_name`, `role`, `email`, `phone_number`, `avatar_url`) |
| `PUT` / `PATCH` | `/users/me` | Authenticated User | Update profil user yang sedang login (`username`, `email`, `phone_number`, `nomor_wa_admin`) dengan validasi keunikan |
| `POST` | `/users/me/change-password` | Authenticated User | Ubah password akun user yang sedang login dengan memverifikasi `old_password` terlebih dahulu |
| `POST` | `/users/me/avatar` | Authenticated User | Upload foto avatar akun internal langsung di-stream ke Cloudinary (`toti-cakery/avatars/`, maks 5MB, format JPEG/PNG/WEBP), simpan `secure_url` ke database |
| `GET` | `/users` | Owner Only | List seluruh akun pengguna internal (Owner, Admin, Staff). Dilindungi guard RBAC: Admin & Staff ditolak (403 Forbidden) |
| `POST` | `/users` | Owner Only | Daftarkan akun internal baru (Owner, Admin, atau Staff). Response mengembalikan `UserOut` lengkap (`id`, `username`, `role_id`, `role_name`, `role`, `handles_takeover`, `is_active`, dll.) dengan relasi `role` yang di-eager load |
| `PUT` / `PATCH` | `/users/{user_id}` | Owner Only | Edit data akun pengguna internal lain oleh Owner (role, handles_takeover, status aktif, reset password, dll.). Owner tidak bisa menonaktifkan diri sendiri |
| `PATCH` | `/users/{user_id}/deactivate` | Owner Only | Deaktivasi akun pengguna internal (`is_active = False`) oleh Owner. Owner tidak bisa menonaktifkan diri sendiri |
| `DELETE` | `/users/{user_id}` | Owner Only | Hapus akun pengguna internal (atau soft-deactivate jika terdapat riwayat transaksi) oleh Owner |
| `PATCH` | `/users/{user_id}/takeover-handler` | Owner Only | Set status apakah admin tersebut bertugas menangani live takeover |
| `GET` | `/users/owner-numbers` | `X-Service-Key` | Ambil daftar seluruh nomor WhatsApp berformat E.164 (tanpa '+') milik user aktif dengan role Owner (Level 1) untuk keperluan verifikasi hak akses pada service Chatbot |

---

### N. Outgoing Webhooks ke Chatbot Service

Backend FastAPI mengirimkan notifikasi HTTP asynchronous (fire-and-forget, non-blocking) ke service Chatbot saat terjadi event-event penting pada pesanan. Seluruh webhook ini dijamin dieksekusi **SETELAH `db.commit()`** berhasil dilakukan di database backend untuk mencegah *race condition* (misalnya Chatbot langsung memanggil `GET /payments/{id}/status` atau `/orders/{id}` namun mendapati data belum committed):

| Target Chatbot Endpoint | Pemicu (Trigger Event) | Header Autentikasi | Request Body | Deskripsi |
| :--- | :--- | :--- | :--- | :--- |
| `POST {CHATBOT_URL}/webhook/internal/orders/{order_id}/ready` | Update status pesanan ke `ready` (`PATCH /orders/{order_id}/status`) | `X-Internal-Key: <CHATBOT_INTERNAL_KEY>` | *(None / Empty)* | Memberitahu Chatbot agar mengirim pesan WA ke pelanggan bahwa pesanan kue sudah selesai dan siap diambil/dikirim. |
| `POST {CHATBOT_URL}/webhook/internal/orders/{order_id}/paid` | Transaksi pembayaran berhasil settlement DP / Lunas (`_apply_transaction_status` pada Midtrans webhook & status check) | `X-Internal-Key: <CHATBOT_INTERNAL_KEY>` | *(None / Empty)* | Memberitahu Chatbot agar mengirim notifikasi konfirmasi pembayaran berhasil ke WhatsApp pelanggan. |
| `POST {CHATBOT_URL}/webhook/internal/orders/{order_id}/refunded` | **2 Sinyal Pemicu** (Lihat rincian di bawah):<br>1. Sinyal Auto Refund (QRIS API sukses)<br>2. Sinyal Manual Refund Completed (Admin mark as `refunded`) | `X-Internal-Key: <CHATBOT_INTERNAL_KEY>` | *(None / Empty)* | Memberitahu Chatbot bahwa dana pesanan pelanggan telah berhasil direfund sehingga Chatbot dapat meneruskan notifikasi WhatsApp ke pelanggan. |

#### Skenario 2 Pemicu Webhook Refund (`/refunded`):
1. **Sinyal 2.a: Auto Refund (QRIS Direct Refund Successful)**
   - **Pemicu**: Dipicu di `payment_service.py` saat API Direct Refund Midtrans sukses (`refund_mode == "auto"`).
   - **Status Pesanan**: Langsung bertransisi menjadi `refunded` dan `payment_status: refunded`.
   - **Waktu Eksekusi**: Ditembakkan tepat **SETELAH `await db.commit()`** pada database backend.
2. **Sinyal 2.b: Manual Refund Completed (Admin Mark as Refunded)**
   - **Pemicu**: Dipicu di `order_service.py` saat Admin/Owner menandai proses refund manual telah selesai via Dashboard Site (`PATCH /orders/{order_id}/status` ke `refunded` atau `POST /orders/{order_id}/refund`).
   - **Status Pesanan**: Bertransisi dari `cancelled` (atau `pending`/`in_process`) -> `refunded`.
   - **Waktu Eksekusi**: Ditembakkan tepat **SETELAH `await db.commit()`** pada database backend.
   - *Catatan*: Jika refund dilakukan via Chatbot (`X-Service-Key`) dan Midtrans mengembalikan status 412 (perlu transfer manual oleh seller), status pesanan menjadi `cancelled` dan webhook `/refunded` **TIDAK** ditembakkan sampai Admin menyelesaikan transfer manual dan mengubah status menjadi `refunded`.

#### Struktur Response Endpoint Refund (`POST /orders/{order_id}/refund`):
```json
{
  "message": "Order refund processed successfully",
  "order_id": 57,
  "status": "refunded", // "refunded" jika auto refund Midtrans sukses atau diproses oleh Admin/Owner; "cancelled" jika via Chatbot fallback manual transfer
  "payment_status": "refunded",
  "refund_mode": "auto" // "auto" (direct API Midtrans sukses) atau "manual" (metode VA/QRIS 412 yang mewajibkan transfer manual seller)
}
```