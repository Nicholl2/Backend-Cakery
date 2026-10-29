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
| **Chatbot & Internal Services** | Pre-shared Service Key | `X-Service-Key: <key>` / `?service_key=<key>` (atau `X-Internal-Key`) | Komunikasi headless dengan scoping granular: `orders:create`, `orders:read`, `orders:cancel`, `payments:create`, `payments:read`, `customers:manage`, `refunds:request`, `reports:summary`, `owners:read`, `settlements:check`, `identity:verify` |
| **Public Webhook Midtrans** | SHA512 Signature Hash | Signature di payload webhook | Verifikasi integritas pembayaran Midtrans tanpa token |
| **Public Endpoint** | Tanpa Autentikasi | - | Katalog produk, FAQ, pencarian konten publik, cek status ulasan |

### B. Validasi Input & Pencegahan XSS

Seluruh endpoint menerapkan perlindungan ketat (Hardening) pada level skema payload:
- **Phone Number / WhatsApp**: Hanya menerima karakter angka. Panjang 10-16 karakter. Seluruh nomor otomatis diubah ke format internasional **E.164** (`628...`).
- **Username**: Maksimal 25 karakter. Hanya boleh berisi huruf, angka, `_`, dan `-` (regex: `^[a-zA-Z0-9_-]+$`).
- **Email**: Format email standar, maksimal 100 karakter.
- **Teks Bebas (Nama, Alamat, Notes, Review)**: Maksimal karakter ketat diberlakukan (contoh: notes/alamat maks 500 karakter, komentar review maks 1000). 
- **Pencegahan XSS / Script Injection**: Seluruh field berupa string/teks akan menolak string yang mengandung tag berbahaya (seperti `<script>`, `javascript:`, `<iframe>`, `<object>`, `<form>`). API akan langsung mengembalikan HTTP 422 Unprocessable Entity atau HTTP 400 Bad Request jika mendeteksi payload berbahaya.
- **Pencarian publik**: Query pencarian dibatasi 2–80 karakter setelah normalisasi spasi, menolak tag `<`/`>` dan karakter kontrol, membatasi 30 request/menit/IP, dan memakai parameter query terikat serta wildcard literal pada pencarian database. Hasil JSON harus dirender frontend sebagai teks biasa, bukan HTML mentah.
- **Upload Size Limit (Max 5MB)**: Middleware `MaxBodySizeMiddleware` menolak setiap request yang payload/body-nya melebihi 5MB (5,242,880 bytes) dengan response `HTTP 413 Payload Too Large`.
- **Spending Cap / Transaction Limit**: Proteksi nilai pesanan anomali membatasi transaksi maksimal **Rp 50.000.000** per order pada `create_new_order` dan `create_custom_order`. Request yang melebihi batas ini ditolak dengan `HTTP 400 Bad Request`.

---

## 2. Daftar Endpoint per Modul

### A. Autentikasi & Verifikasi Akun (`/auth`)

| Method | Endpoint | Auth / Permission | Deskripsi |
| :--- | :--- | :--- | :--- |
| `POST` | `/auth/bootstrap` | Public (First-time only) | Inisialisasi akun Owner pertama jika database masih kosong |
| `POST` | `/auth/login` | Public | Login akun internal (Owner/Admin/Staff), return JWT token & role level |
| `POST` | `/auth/verify/wa/start` | Public | Memulai sesi verifikasi WA (E.164 7-15 digit). Di mode real: return `nonce` & `deeplink`. Di mode mock (`WA_VERIFICATION_MODE=mock` non-prod): otomatis verifikasi dan return `verify_token` & `mock_mode: true` |
| `POST` | `/auth/verify/wa/confirm` | `X-Service-Key` (`SERVICE_WA_VERIFICATION_KEY`) / `X-Internal-Key` | Konfirmasi nomor pengirim oleh chatbot saat customer mengirim pesan verifikasi WhatsApp (scope `identity:verify`) |
| `GET` | `/auth/verify/wa/status` | Public (Polling) | Cek status verifikasi WA berdasarkan `nonce`. Di mode mock/verified: return status `"verified"` & `verify_token` |
| `POST` | `/auth/buyer/register` | Public (`verify_token`) | Registrasi akun pembeli baru menggunakan token verifikasi WA dan nomor telepon valid |
| `POST` | `/auth/buyer/login` | Public | Login pembeli via email/password atau phone/`verify_token` |
| `POST` | `/auth/buyer/forgot-password` | Public | Request OTP reset password ke email buyer |
| `POST` | `/auth/buyer/reset-password/email` | Public | Reset password buyer via email, kode OTP, dan password baru |
| `POST` | `/auth/buyer/login-phone` | Public | Login pembeli via nomor telepon (E.164 internasional) dan password |
| `POST` | `/auth/buyer/login/otp` | Public (`verify_token`) | Login pembeli via nomor telepon dan token verifikasi WA |
| `POST` | `/auth/buyer/reset-password` | Public (`verify_token`) | Reset password pembeli menggunakan token verifikasi |
| `POST` | `/auth/seller/forgot-password/request` | Public | Request OTP reset password untuk akun internal/seller |
| `POST` | `/auth/seller/forgot-password/verify` | Public | Verifikasi kode OTP seller, return `verify_token` |
| `POST` | `/auth/seller/reset-password` | Public (`verify_token`) | Reset password akun seller menggunakan token verifikasi |
| `POST` | `/auth/logout` | JWT Authentication (Bearer) | Revoke sesi aktif dan blacklist token JWT (JTI & Signature) ke in-memory cache hingga masa berlaku token habis (durasi token default: 60 menit) |

---

### B. Produk & Katalog (`/products`)

| Method | Endpoint | Auth / Permission | Deskripsi |
| :--- | :--- | :--- | :--- |
| `POST` | `/products/` | Admin / Owner | Buat master produk baru (HPP default 0 sebelum resep diisi, `is_available` default `true`) |
| `GET` | `/products/` | Public | List katalog produk (Filter: `only_active`, `kategori`, `only_available`). Dilengkapi **in-memory TTL cache (5 menit)** dengan auto-invalidation saat ada operasi create/update/delete/pricing/image produk. Default menampilkan semua produk aktif (termasuk yang stok habis). Field response memuat `is_available: bool` (manual seller), `stock_quantity: int` (ketersediaan bahan/resep), dan `is_in_stock: bool` (computed property). Filter `only_available=true` hanya mereturn produk dengan `is_in_stock == true`. |
| `GET` | `/products/{product_id}` | Public | Detail produk lengkap beserta ketersediaan manual (`is_available`), stok bahan (`stock_quantity`), status siap beli (`is_in_stock`), `hpp_total`, dan advisory `warning_below_hpp` untuk harga tersimpan |
| `PUT` | `/products/{product_id}` | Admin / Owner | Update data produk termasuk harga jual. Response mengembalikan `hpp_total` dan `warning_below_hpp`; harga di bawah HPP tetap boleh disimpan. Perubahan harga dicatat di riwayat beserta HPP saat penyimpanan dan ID user terautentikasi. |
| `DELETE` | `/products/{product_id}` | Admin / Owner | Hapus produk beserta seluruh relasi resep dan riwayat harganya |
| `POST` | `/products/{product_id}/images` | Admin / Owner | Upload multiple foto produk langsung ke Cloudinary (`toti-cakery/products`, maks 5MB per file, format JPEG/PNG/WEBP), simpan array secure URLs ke tabel `product_images`, dan set gambar pertama/pilihan sebagai primary image |
| `POST` | `/products/{product_id}/image` | Admin / Owner | Upload gambar produk tunggal (Backward Compatibility) ke Cloudinary |
| `PATCH` | `/products/{product_id}/images/{image_id}/primary` | Admin / Owner | Tetapkan salah satu gambar produk sebagai primary image (otomatis sinkronisasi `products.image_url`) |
| `DELETE` | `/products/{product_id}/images/{image_id}` | Admin / Owner | Hapus salah satu gambar dari galeri produk (dan hapus resource Cloudinary jika memungkinkan; reassign primary ke gambar berikutnya jika gambar utama dihapus) |
| `PATCH` | `/products/{product_id}/price` | Admin / Owner | Tetapkan/ubah harga jual produk; HPP dihitung ulang dari resep dan average cost terkini, response menyertakan `warning_below_hpp` dan margin, harga di bawah HPP tetap diperbolehkan, dan riwayat harga dicatat beserta ID user terautentikasi |
| `GET` | `/products/{product_id}/pricing` | Public / Internal | Lihat rincian breakdown HPP bahan + kalkulasi margin vs harga jual, termasuk flag `warning_below_hpp`; HPP dihitung dari resep dan average cost bahan saat ini |
| `GET` | `/products/{product_id}/price-history`| Public / Internal | Riwayat perubahan harga jual produk oleh Owner |

Untuk peringatan langsung saat seller mengetik harga, FE memakai `hpp_total` dari detail produk (atau `hpp` dari `/products/{product_id}/pricing`) dan membandingkannya dengan nilai harga di form: `harga_jual < HPP` berarti tampilkan warning, tetapi jangan blokir tombol simpan. `ProductOut` mengembalikan `warning_below_hpp` sebagai pemeriksaan harga tersimpan; endpoint pricing juga mengembalikan flag dan margin. Jika HPP `0` atau produk belum punya resep, tampilkan bahwa HPP belum tersedia dan jangan tandai harga sebagai aman/untung.


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
| `POST` | `/stock/` | Internal | Buat satu master material baru (memastikan default `alert_min_stok = 0.00` konsisten), opsional dengan saldo awal yang menghasilkan `OPENING_BALANCE`. Supplier pembelian dipilih pada Purchase; endpoint ini tidak membuat item untuk restock. |
| `GET` | `/stock/` | Internal | List semua item stok (Filter: `bahan_baku` / `kemasan`). Untuk form purchase, tampilkan `satuan` sebagai unit dasar dan `harga_per_satuan` sebagai average cost saat ini. |
| `GET` | `/stock/{stock_id}` | Internal | Detail master bahan (stok terkini, average cost, alert minimum) |
| `PUT` | `/stock/{stock_id}` | Internal | Update nama, satuan, kategori, dan batas stok minimum. Saldo serta average cost tidak dapat diedit langsung; koreksi melalui transaksi inventory yang tercatat. |
| `POST` | `/stock/{stock_id}/adjustments` | Admin / Owner | Koreksi stok dengan validasi bilangan riil terbatas (`quantity_difference` & `unit_cost` finite, menolak `NaN`/`Infinity`), pencegahan saldo negatif (stok tidak boleh di bawah 0), `reason` wajib, dan membuat ledger `StockMovement` tercatat. |
| `GET` | `/stock/{stock_id}/movements` | Internal | Histori stock movement untuk audit. |
| `DELETE` | `/stock/{stock_id}` | Admin / Owner | Hapus hanya jika tidak punya saldo, recipe, atau histori movement. |

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
| `GET` | `/purchases/purchases` | Internal | List PO (filter: `only_received`, `only_cancelled`, `supplier_id`). Tanpa filter menampilkan seluruh status. |
| `GET` | `/purchases/purchases/{purchase_id}` | Internal | Detail PO beserta item; tiap item mengembalikan unit dasar dan average cost terkini pada `stock_item.base_unit` dan `stock_item.average_cost`. |
| `PUT` | `/purchases/purchases/{purchase_id}` | Admin / Owner | Purchase yang menunggu dapat mengubah supplier, nomor PO, catatan, dan seluruh daftar item. Kirim daftar item lengkap; tiap item memakai `stock_item_id`, `jumlah` dalam unit dasar, dan tepat satu dari `harga_total` atau legacy `harga_satuan`. `harga_total` direkomendasikan; server menurunkan harga per unit. Terima purchase lewat `is_received=true` pada request terpisah. Item/identitas PO yang sudah diterima tidak dapat diedit; catatan masih dapat diperbarui. Purchase batal tidak dapat diedit atau diterima. |
| `POST` | `/purchases/purchases/{purchase_id}/cancel` | Admin / Owner | Batalkan purchase yang belum diterima dengan body `{"reason":"Salah input harga"}`. Record tidak dihapus; alasan, waktu, dan aktor tersimpan untuk audit. Purchase diterima atau sudah batal ditolak dengan HTTP 409. |
| `DELETE` | `/purchases/purchases/{purchase_id}` | Admin / Owner | Alias kompatibilitas untuk membatalkan PO yang belum diterima; tidak menghapus record. Gunakan endpoint `POST .../cancel` agar alasan tercatat. |

Contoh item purchase baru: `{"stock_item_id":12,"jumlah":100,"harga_total":250000}` berarti 100 unit dasar dengan total Rp250.000; server menghitung Rp2.500 per unit. FE perlu menampilkan nama item, unit dasar, average cost saat ini, jumlah yang dibeli (dalam unit dasar), dan harga total yang ditagihkan. Jika harga per unit hasilnya berbeda minimal 10× dari average cost sekarang, API menolak dengan HTTP 422 kecuali client mengirim `confirm_cost_deviation=true` setelah pengguna mengonfirmasi peringatan. Jumlah, harga, total purchase, presisi database, dan ID item duplikat divalidasi.

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
| `POST` | `/buyers/me/change-password` (alias: `/v1/buyers/me/change-password`) | Buyer JWT (`get_current_buyer`) | Ganti password Buyer yang sedang login. Wajib menyertakan `current_password` dan `new_password` (min 6 karakter). Tanpa OTP. |
| `PATCH` | `/buyers/me/phone` (alias: `/v1/buyers/me/phone`) | Buyer JWT (`get_current_buyer`) | Ubah nomor WhatsApp Buyer. Wajib verifikasi `current_password`. Nomor baru harus unik (belum terdaftar buyer lain). Return `BuyerProfileResponse` dengan nomor terbaru. Tanpa OTP. |


---

### G. Pemesanan / Orders (`/orders`)

| Method | Endpoint | Auth / Permission | Deskripsi |
| :--- | :--- | :--- | :--- |
| `GET` | `/orders` | Staff / Admin / Owner | List seluruh pesanan toko untuk Seller (Staff/Admin/Owner) dengan relasi lengkap (Customer, OrderItems, Invoice, Payments, amount_paid, amount_due, fulfillment_date, settlement_due_date, `payment_expires_at`, dan `fulfillment_overdue_notified_at`). Filter: `status`, `limit`, `offset`. |
| `GET` | `/orders/stats` | Staff / Admin / Owner | Ringkasan statistik pesanan toko (total orders, count per status, active/completed orders, dan total revenue). Parameter query tanggal `start_date` dan `end_date` bersifat opsional (default: seluruh/rentang aman tanpa memicu error HTTP 422). |
| `GET` | `/orders/{order_id}` | Staff / Admin / Owner (JWT) OR `SERVICE_ORDERS_READ_KEY` (`X-Service-Key` / `?service_key=...`) | Detail pesanan spesifik (`OrderOut`) lengkap beserta metadata customer/buyer, item produk (dengan field `product_name`), invoice, `fulfillment_date`, `settlement_due_date`, `payment_expires_at`, `fulfillment_overdue_notified_at`, ringkasan pembayaran, dan `payment_status`. Dilindungi guard `require_order_read_access` (scope `orders:read`). |
| `POST` | `/orders/custom` | Staff / Admin / Owner | Membuat order manual terkonfirmasi: `items[]` memakai `product_id`, `qty`, dan `custom_decoration_charge`; `accessories[]` opsional memakai `stock_item_id` dan `quantity`; `fulfillment_date` wajib. Harga produk memakai katalog, biaya aksesoris memakai snapshot `harga_per_satuan` stok saat disimpan, dan stok resep + aksesoris dikurangi atomik serta dicatat sebagai movement `ORDER`. Menghasilkan invoice dan set `created_via = 'seller'`. Tidak mengirim email otomatis. Contract lengkap: [`CUSTOM_ORDER_API_CONTRACT.md`](CUSTOM_ORDER_API_CONTRACT.md). |
| `POST` | `/orders/buyer` | Buyer JWT (`get_current_buyer`) | Buat order baru khusus Buyer: (1) Wajib menyertakan `fulfillment_date` minimal H+1 dari tanggal order dibuat (Same-day ditolak HTTP 400); (2) Otomatis derive `customer_id` via logika `get_or_create` berbasis varian nomor HP untuk mencegah error 409 Conflict; (3) Generate `nomor_invoice` unik dengan suffix acak; (4) Validasi ketersediaan produk `is_in_stock` & batas `stock_quantity`, reservasi bahan via Pessimistic Row Locking (`SELECT ... FOR UPDATE` berurut ID bahan) dan Optimistic Locking; (5) Exception handling terpusat yang mereturn HTTP 400 jika ada kesalahan data. |
| `GET` | `/orders/buyer` | Buyer JWT (`get_current_buyer`) | Ambil seluruh riwayat pesanan milik Buyer yang sedang login (eager-loaded dengan customer, order_items, invoice, payments, fulfillment_date, settlement_due_date). |
| `GET` | `/orders/buyer/{id}` | Buyer JWT (`get_current_buyer`) | Detail pesanan spesifik milik Buyer (isolasi data aman antarpembeli, 404 jika bukan milik Buyer yang login). |
| `POST` | `/orders` | `X-Service-Key` | Buat order baru via chatbot (wajib `fulfillment_date` minimal H+1 dan tersimpan, validasi ketat ketersediaan produk `is_in_stock` & batas `stock_quantity` -> HTTP 400 jika habis/melebihi stok, reservasi stok bahan via Pessimistic Row Locking `SELECT ... FOR UPDATE` berurut ID bahan untuk mencegah overselling pada request serentak, Optimistic Locking, generate invoice). |
| `GET` | `/orders/latest` | `X-Service-Key` | Ambil order terbaru pelanggan berdasarkan query `?nomor_wa=...` |
| `POST` | `/orders/{order_id}/cancel` | `X-Service-Key` | Pembatalan otomatis oleh pelanggan (hanya jika invoice `unpaid`, stok bahan dikembalikan). |
| `PATCH` | `/orders/{order_id}/status` | Staff / Admin / Owner | Update status pesanan (`pending`, `in_process`, `ready`, `delivered`, `picked_up`, `completed`, `cancelled`, `refunded`, `cancelled_settlement_expired`). Memvalidasi aturan transisi, pembayaran minimum DP 50% untuk mulai proses, dan otorisasi internal. Dibatasi 30 request/menit per IP. Status order buyer dienqueue ke email outbox; event Chatbot dikirim setelah commit untuk `processing`, `ready`, `delivered`, `picked_up`, `completed`, `cancelled`, dan `refunded`. |
| `POST` | `/orders/{order_id}/refund` | Staff/Admin/Owner OR `X-Service-Key` | Memproses pembatalan sekaligus refund untuk pesanan berstatus DP/Lunas. Memicu API Midtrans, rollback stok bahan, dan trigger webhook `/refunded` ke Chatbot SETELAH database commit. Jika dipanggil Chatbot (`X-Service-Key`), memvalidasi kepemilikan `nomor_wa` dan status wajib `pending`. Mengembalikan payload DTO dengan penanda `refund_mode` (`auto` jika direct API Midtrans berhasil dengan status akhir order `refunded`, atau `manual` jika metode VA/QRIS 412 yang mewajibkan transfer manual dengan status order `cancelled`). Jika dipanggil oleh Admin/Seller, pesanan langsung bertransisi menjadi `refunded` dan memicu sinyal 2.b webhook `/refunded`. |
| `GET` | `/orders/{id}/invoice/pdf` | Buyer Pemilik / Staff / Admin / Owner | Generate dan unduh PDF Invoice pesanan Toti Cakery (ReportLab). Mengembalikan `StreamingResponse` dengan `Content-Type: application/pdf` dan header `Content-Disposition: attachment; filename="Invoice-TotiCakery-{order_id}.pdf"`. Eager-loading order, customer, items, product, invoice, dan payments. Buyer dibatasi hanya untuk pesanan miliknya (404 jika bukan pemilik). |

---

### H. Pembayaran Midtrans Core API (`/payments`)

| Method | Endpoint | Auth / Permission | Deskripsi |
| :--- | :--- | :--- | :--- |
| `POST` | `/payments` | `X-Service-Key` / Buyer JWT | Charge pembayaran ke Midtrans (Bank Transfer BCA VA atau QRIS). Validasi nominal anti-tampering (tipe `full` 100%, tipe `dp` tepat 50% dari total tagihan), hitung `settlement_due_date` otomatis (H-1 sebelum `fulfillment_date` pukul 18:00 WIB), dan verifikasi kepemilikan order untuk Buyer. Jika hasil charge tidak pasti, API mengembalikan `502` dan menyimpan reservasi; retry hanya dengan metode/tipe/nominal yang sama akan merekonsiliasi reservasi tersebut. Gunakan `GET /payments/{order_id}/status` untuk memeriksa status dan mengambil kembali instruksi. Jangan membuat charge baru dengan parameter berbeda selama status sebelumnya masih pending (`409`). |
| `POST` | `/payments/manual` | Staff / Admin / Owner (JWT) / System | Catat transaksi pembayaran manual (CASH, TRANSFER, dll.). Mengubah `payment_status` pesanan menjadi `PAID`, mempertahankan status order tetap `pending` (agar konsisten dengan alur pembayaran dan dapat dibatalkan bila perlu), serta membuat record pembayaran baru di database (beserta kolom `notes`) |
| `GET` | `/payments/{order_id}/status` | `X-Service-Key` / Buyer JWT | Cek status tagihan, total terbayar, sisa tagihan, dan refresh transaksi pending untuk Chatbot & Buyer |
| `POST` | `/payments/notify` (alias: `/payments/notification`) | Public Webhook | Listener webhook otomatis Midtrans (validasi signature SHA512, defensive payload check, auto-settlement invoice & order, idempotensi pencocokan transaksi direct `Payment.pg_order_id == order_id` dengan row-level locking `with_for_update()` sebelum invoice fallback, 200 OK acknowledgment jika order tidak ditemukan di database) |
| `POST` | `/payments/check-expired-settlements` | `X-Service-Key` / Internal JWT | Background task internal untuk memeriksa pesanan DP (`partial`) yang telah melewati `settlement_due_date`, otomatis membatalkan pesanan menjadi `cancelled_settlement_expired` dan me-rollback stok bahan ke gudang |

### Seller Email Sender Settings (`/settings`)

| Method | Endpoint | Auth / Permission | Deskripsi |
| :--- | :--- | :--- | :--- |
| `GET` | `/settings/email-sender` | Owner JWT | Baca alamat/nama pengirim email aktif; fallback ke `SMTP_FROM_EMAIL`/`SMTP_FROM_NAME` jika belum disetel di database. |
| `PATCH` | `/settings/email-sender` | Owner JWT | Ubah `from_email` dan `from_name` untuk email yang dikirim berikutnya. Batas 5 request/menit. Memvalidasi format email, panjang nama, dan menolak karakter kontrol. Kredensial SMTP tidak dibaca/ditulis oleh endpoint ini. Alamat harus sudah diverifikasi di provider email sebelum dipakai. |

Order lifecycle automation dijalankan oleh worker aplikasi saat `ORDER_LIFECYCLE_WORKER_ENABLED=true`. Worker membatalkan checkout unpaid setelah timeout (default 60 menit), setelah merekonsiliasi transaksi Midtrans yang masih pending; menjalankan pembatalan settlement DP yang melewati `settlement_due_date`; dan mengirim alert satu kali untuk order berbayar yang melewati tanggal pemenuhan. Field `payment_expires_at` dan `fulfillment_overdue_notified_at` tersedia di respons order. Keterlambatan pemenuhan tidak memicu refund otomatis. Untuk worker pada deployment persisten, atur `ORDER_LIFECYCLE_WORKER_ENABLED=true`; deployment serverless memerlukan worker persisten terpisah.

---

### I. Biaya Operasional / Expenses (`/expenses`)

| Method | Endpoint | Auth / Permission | Deskripsi |
| :--- | :--- | :--- | :--- |
| `POST` | `/expenses` | Admin / Owner | Catat expense dengan `kategori`, `jumlah` (> 0), `tanggal?`, `deskripsi?`, `penerima?`, `metode_pembayaran?` (`cash`, `bank_transfer`, `e_wallet`, `card`, `other`), dan `bukti_url?` (HTTPS). Membuat audit event `created`. |
| `GET` | `/expenses` | Authenticated User | List riwayat, termasuk yang sudah dibatalkan (status `is_voided`). Filter: `kategori`, `start_date`, `end_date`, paginasi. |
| `GET` | `/expenses/summary/dashboard` | Authenticated User | Ringkasan total, breakdown kategori, dan count dengan filter tanggal yang konsisten; expense yang dibatalkan dikecualikan. |
| `PATCH` | `/expenses/{expense_id}` | Admin / Owner | Partial update field expense yang boleh diedit; butuh minimal satu field dan menyimpan perubahan before/after ke audit. Expense yang dibatalkan tidak bisa diedit (`409`). |
| `POST` | `/expenses/{expense_id}/cancel` | Admin / Owner | Soft-cancel dengan body `{"reason":"..."}` (3–500 karakter). Tidak menghapus record; expense void tidak masuk laporan. Cancel ulang ditolak (`409`). |
| `GET` | `/expenses/{expense_id}` | Authenticated User | Detail satu expense termasuk metadata dan status cancel. |
| `GET` | `/expenses/{expense_id}/audit` | Admin / Owner | Riwayat event `created`, `updated`, `voided` beserta actor dan before/after. |

---

### J. Laporan & Analitik (`/reports`)

| Method | Endpoint | Auth / Permission | Deskripsi |
| :--- | :--- | :--- | :--- |
| `GET` | `/reports/summary` | Staff / Admin / Owner (JWT) | Ringkasan metrik dashboard Frontend Admin Site: `total_products` (int), `active_products` (int), `total_revenue` (Decimal), `total_orders` (int), dan `recent_orders` (5 pesanan terbaru lengkap dengan ID, nama customer, total harga, dan status) |
| `GET` | `/reports/financial` | Staff / Admin / Owner (JWT) | Laporan komprehensif Laba/Rugi (P&L): `revenue`/`total_revenue`, `cash_received` (akumulasi arus kas masuk riil periode ini dengan integritas historis), `cash_refunded` (total arus kas keluar via refund pada periode ini), `net_cash_flow` (`cash_received - cash_refunded`), `hpp_total`/`total_hpp_cost`, `gross_profit`, `expenses_total`/`total_expenses`, `net_profit` (termasuk pendapatan DP hangus), `outstanding_payments` (akumulasi piutang kumulatif seluruh order non-cancelled per titik `end_date`), `non_refundable_dp_income`/`other_income` (pendapatan lain-lain dari DP pesanan yang dibatalkan tanpa refund), `product_profitability`/`full_product_profitability` (rincian qty terjual, revenue, HPP, gross profit, margin % per produk), dan `supplier_spending` (total belanja dan frekuensi PO per supplier). Menggunakan basis tanggal pembayaran lunas yang konsisten (menghindari pergeseran periode) dan mengeluarkan seluruh order unpaid/pending dari HPP & profit. |
| `GET` | `/reports/financial-summary` | `SERVICE_FINANCIAL_SUMMARY_KEY` (`X-Service-Key` / `?service_key=...`) / Internal JWT | Ringkasan cepat laporan keuangan (`FinancialReportSummary`) untuk Chatbot atau internal: omzet kotor (`omzet_kotor`), modal HPP (`modal_hpp`), laba kotor (`laba_kotor`), margin persen (`margin_persen`), total pesanan (`total_pesanan`), dan rata-rata nilai pesanan (`aov`). Scope `reports:summary`. |
| `GET` | `/reports/analytics` | Staff / Admin / Owner (JWT) | Analitik penjualan bulanan, tren produk terlaris, dan distribusi rating |
| `GET` | `/reports/sales-trends` | Staff / Admin / Owner (JWT) | Seri grafik berisi `period_start`, `revenue`, `hpp`, `gross_profit`, `order_count`. Query `start_date`, `end_date` (`YYYY-MM-DD`), `granularity` (`day` default, `week`, `month`). Menggunakan tanggal settlement akhir order dan definisi paid-order yang sama dengan laporan finansial; periode kosong tetap dikembalikan dengan nilai nol. Batas rentang: 366 hari untuk daily, 3650 hari weekly, 7300 hari monthly. |

---

### K. Ulasan Produk / Reviews (`/reviews`)

| Method | Endpoint | Auth / Permission | Deskripsi |
| :--- | :--- | :--- | :--- |
| `POST` | `/reviews/` | Buyer Auth | Buat ulasan produk baru. Mendukung **JSON body** (`order_id`, `product_id`, `rating`, `comment`) maupun **multipart/form-data** dengan upload multiple file foto ulasan (`images`). Foto di-stream langsung ke folder Cloudinary `toti-cakery/reviews/`. **Validasi ketat**: Pesanan harus ada & berstatus selesai (`completed` / `delivered` / `picked_up`), produk harus merupakan item pesanan terkait, dan 1 item pesanan tidak dapat diulas lebih dari satu kali (*duplicate protection*). Response menggunakan `ReviewPublicOut`; `order_id` dan `customer_id` hanya input/relasi internal, tidak dikembalikan. |
| `POST` | `/reviews/{review_id}/images` | Buyer Author | Upload multiple foto baru ke ulasan yang sudah ada (khusus pemilik ulasan). File gambar maks 5MB/file (JPEG/PNG/WEBP). |
| `DELETE` | `/reviews/{review_id}/images/{image_id}` | Buyer Author / Admin / Owner | Hapus salah satu foto dari ulasan produk dan otomatis menghapusnya dari storage Cloudinary. |
| `GET` | `/reviews/latest` | Public | List ulasan terbaru global (`limit`, default 6) dengan nama produk/pelanggan dan `images`. Response memakai `ReviewPublicOut`: `id`, `product_id`, `rating`, `comment`/`komentar`, `created_at`, `product_name`, `customer_name`, `product` (`nama_produk`, `image_url`), `customer` (`nama`), dan `images`. Tidak mengirim `order_id`, `customer_id`, nomor WA/alamat, atau data internal produk seperti HPP, stok, dan resep. |
| `GET` | `/reviews/product/{product_id}` | Public | List ulasan untuk produk dengan schema aman `ReviewPublicOut`; tidak menyertakan kontak pelanggan atau rincian order/internal produk. |
| `GET` | `/reviews/{review_id}` | Public | Detail ulasan dengan schema aman `ReviewPublicOut` dan relasi foto. |
| `PUT` | `/reviews/{review_id}` | Buyer Author | Edit rating / komentar ulasan milik sendiri |
| `DELETE` | `/reviews/{review_id}` | Buyer Author / Admin / Owner | Hapus ulasan beserta seluruh foto terkait (otomatis hapus dari Cloudinary dan rekalkulasi rata-rata rating produk) |



---

### L. Tanya Jawab / FAQ Management (`/faq`)

| Method | Endpoint | Auth / Permission | Deskripsi |
| :--- | :--- | :--- | :--- |
| `POST` | `/faq` | Admin / Owner | Tambah item pertanyaan & jawaban FAQ baru |
| `GET` | `/faq` | Public | List FAQ (Filter: `only_active`, paginasi). Dilengkapi **in-memory TTL cache (5 menit)** dengan auto-invalidation saat ada operasi create/update/delete FAQ. |
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
| `POST {CHATBOT_URL}/webhook/internal/orders/{order_id}/created` | Pesanan baru dibuat (`POST /orders` chatbot & `POST /orders/buyer` website) | `X-Internal-Key: <CHATBOT_INTERNAL_KEY>` | *(None / Empty)* | Memberitahu Chatbot agar mengirim notifikasi WA pesanan baru ke seller (padanan email `order_created`); Chatbot membaca detail order lewat `GET /orders/{order_id}` (`event: "created"`). |
| `POST {CHATBOT_URL}/webhook/internal/orders/{order_id}/processing` | Pesanan mulai diproses (`in_process`) via update status atau auto-settlement | `X-Internal-Key: <CHATBOT_INTERNAL_KEY>` | *(None / Empty)* | Memberitahu Chatbot agar mengirim pesan WA ke pelanggan bahwa pesanan kue sedang diproses oleh dapur (`event: "processing"`). |
| `POST {CHATBOT_URL}/webhook/internal/orders/{order_id}/ready` | Update status pesanan ke `ready` (`PATCH /orders/{order_id}/status`) | `X-Internal-Key: <CHATBOT_INTERNAL_KEY>` | *(None / Empty)* | Memberitahu Chatbot agar mengirim pesan WA ke pelanggan bahwa pesanan kue sudah selesai dan siap diambil/dikirim (`event: "ready"`). |
| `POST {CHATBOT_URL}/webhook/internal/orders/{order_id}/completed` | Update status pesanan ke `completed` (`PATCH /orders/{order_id}/status`) | `X-Internal-Key: <CHATBOT_INTERNAL_KEY>` | *(None / Empty)* | Memberitahu Chatbot bahwa pesanan telah tuntas diselesaikan/diambil (`event: "completed"`). |
| `POST {CHATBOT_URL}/webhook/internal/orders/{order_id}/delivered` | Update status pesanan ke `delivered` | `X-Internal-Key: <CHATBOT_INTERNAL_KEY>` | *(None / Empty)* | Chatbot mengambil detail order dari backend lalu mengirim pemberitahuan pesanan telah diantar. |
| `POST {CHATBOT_URL}/webhook/internal/orders/{order_id}/picked_up` | Update status pesanan ke `picked_up` | `X-Internal-Key: <CHATBOT_INTERNAL_KEY>` | *(None / Empty)* | Chatbot mengambil detail order dari backend lalu mengirim pemberitahuan pesanan telah diambil. |
| `POST {CHATBOT_URL}/webhook/internal/orders/{order_id}/cancelled` | Order dibatalkan, termasuk checkout unpaid yang kedaluwarsa | `X-Internal-Key: <CHATBOT_INTERNAL_KEY>` | *(None / Empty)* | Chatbot mengambil detail order dan mengabari pelanggan. Event memakai `Idempotency-Key: order:{order_id}:cancelled`. |
| `POST {CHATBOT_URL}/webhook/internal/orders/{order_id}/overdue` | Tanggal pemenuhan terlewati pada order yang sudah membayar | `X-Internal-Key: <CHATBOT_INTERNAL_KEY>` | *(None / Empty)* | Chatbot mengambil detail order dan mengabari pelanggan bahwa seller perlu menyepakati tindak lanjut. Event hanya dikirim sekali per order (`Idempotency-Key: order:{order_id}:overdue`). |
| `POST {CHATBOT_URL}/webhook/internal/orders/{order_id}/paid` | Transaksi pembayaran berhasil settlement DP / Lunas (`_apply_transaction_status` pada Midtrans webhook & status check) | `X-Internal-Key: <CHATBOT_INTERNAL_KEY>` | *(None / Empty)* | Memberitahu Chatbot agar mengirim notifikasi konfirmasi pembayaran berhasil ke WhatsApp pelanggan (`event: "paid"`). |
| `POST {CHATBOT_URL}/webhook/internal/orders/{order_id}/refunded` | **2 Sinyal Pemicu** (Lihat rincian di bawah):<br>1. Sinyal Auto Refund (QRIS API sukses)<br>2. Sinyal Manual Refund Completed (Admin mark as `refunded`) | `X-Internal-Key: <CHATBOT_INTERNAL_KEY>` | *(None / Empty)* | Memberitahu Chatbot bahwa dana pesanan pelanggan telah berhasil direfund sehingga Chatbot dapat meneruskan notifikasi WhatsApp ke pelanggan (`event: "refunded"`). |

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

---

### O. Uptime & Health Check (`/health`)

Endpoint pemantauan status liveness dan konektivitas database untuk load balancer, Docker, atau monitoring service (UptimeRobot, Datadog, Prometheus).

| Method | Endpoint | Auth / Permission | Deskripsi |
| :--- | :--- | :--- | :--- |
| `GET` | `/health` | Public (No Auth) | Memeriksa ketersediaan aplikasi dan konektivitas async database via query `SELECT 1`. |

#### Format Response:
- **HTTP 200 OK** (Database connected):
  ```json
  {
    "status": "ok",
    "database": "connected"
  }
  ```
- **HTTP 503 Service Unavailable** (Database disconnected):
  ```json
  {
    "status": "error",
    "database": "disconnected"
  }
  ```

---

### P. Wishlist (`/buyers/me/wishlist`)

| Method | Endpoint | Auth / Permission | Deskripsi |
| :--- | :--- | :--- | :--- |
| `GET` | `/buyers/me/wishlist` | Buyer Authenticated | List semua wishlist produk buyer login |
| `POST` | `/buyers/me/wishlist/{id}` | Buyer Authenticated | Tambah produk ke wishlist |
| `DELETE` | `/buyers/me/wishlist/{id}`| Buyer Authenticated | Hapus produk dari wishlist |

---

### Q. Manajemen Pembeli / Admin Buyer (`/admin/buyers`)

| Method | Endpoint | Auth / Permission | Deskripsi |
| :--- | :--- | :--- | :--- |
| `GET` | `/admin/buyers` | Admin / Owner | List semua buyer dengan pagination |
| `GET` | `/admin/buyers/{id}` | Admin / Owner | Ambil detail satu buyer spesifik |
| `DELETE`| `/admin/buyers/{id}` | Admin / Owner | Hapus permanen buyer & seluruh wishlist/relasinya |

---

### R. Manajemen WhatsApp Chatbot Admin (`/admin/whatsapp`)

| Method | Endpoint | Auth / Permission | Deskripsi |
| :--- | :--- | :--- | :--- |
| `GET` | `/admin/whatsapp/status` | Admin / Owner (`require_admin_or_owner`) | Mengambil status koneksi WhatsApp chatbot: `{"keadaan": "tersambung" \| "menunggu_scan" \| "terputus", "nomor": str \| null, "profile_name": str \| null}` |
| `GET` | `/admin/whatsapp/qr` | Khusus Owner (`require_owner`) | Mengambil gambar QR code autentikasi WhatsApp (`image/png`, `Cache-Control: no-store`). 404 jika belum siap/sudah scan, 503 jika timeout |
| `POST` | `/admin/whatsapp/ganti-nomor` | Khusus Owner (`require_owner`) | Meminta chatbot mereset sesi dan berganti nomor (timeout >= 65s) dengan pencatatan log audit (`{"status": "ok"}`) |

---

### S. Public Kontak & Toko (`/public`)

| Method | Endpoint | Auth / Permission | Deskripsi |
| :--- | :--- | :--- | :--- |
| `GET` | `/public/kontak-toko` | Public (Tanpa login) | Mengambil nomor WhatsApp aktif toko/chatbot (`{"whatsapp": "..."}`) dengan in-memory caching ±60 detik (fallback ke config jika offline) |
| `GET` | `/public/search?q=bolu` | Public (Tanpa login) | Pencarian lintas konten produk aktif (nama, deskripsi, kategori), FAQ aktif (pertanyaan, jawaban), dan metadata kontak toko. Respons berisi `query`, `products` (`id`, `nama_produk`, `slug`, `deskripsi`, `kategori`, `harga_jual`, `image_url`), `faqs` (`id`, `pertanyaan`, `jawaban`), `store_info` (`key`, `title`, `description`, `action`), dan `total`; maksimal 5 hasil per jenis. `q` wajib 2–80 karakter setelah normalisasi, menolak tag/karakter kontrol, serta dibatasi 30 request/menit/IP dengan limiter in-memory. Item kontak memakai `action: fetch_contact_endpoint`; FE dapat mengambil nomor terbaru dari `/public/kontak-toko`. Backend saat ini belum menyimpan alamat, jam operasional, atau profil toko terstruktur. Tidak memuat pesanan atau data personal. Hasil teks harus dirender FE sebagai teks biasa. |
