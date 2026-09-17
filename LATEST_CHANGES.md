# Ringkasan Perubahan Terbaru & Panduan Testing

Dokumen ini merangkum seluruh perubahan kode terbaru pada Backend Toti Cakery, penjelasan alur logika program, serta panduan langkah demi langkah untuk melakukan pengujian (*testing guide*).

---

## 📌 Daftar Perubahan Kode Terbaru

### 001u. Perbaikan Database Error & Unhandled HTTP 500 pada Endpoint GET /orders & GET /reports/financial (Neon Postgres & pgBouncer)
- **Konfigurasi Engine SQLAlchemy untuk Neon Postgres & pgBouncer (`app/core/database.py`, `app/core/config.py`)**:
  - Menambahkan sanitasi string URL otomatis (`sanitize_db_url`) untuk membersihkan query parameter `sslmode=require` / `ssl=require` agar driver `asyncpg` tidak melempar `TypeError: unexpected keyword argument 'sslmode'`.
  - Menetapkan `connect_args={"ssl": True, "statement_cache_size": 0, "prepared_statement_cache_size": 0}` pada driver `asyncpg` guna mengaktifkan SSL secara aman dan mencegah prepared statement collision error di lingkungan Neon / pgBouncer pooling.
  - Mengonfigurasi `pool_pre_ping=True` dan `pool_recycle=300` pada pembuatan engine untuk mencegah *stale connection* dari Neon serverless sleep.
  - Menambahkan property alias `DATABASE_URL` pada class `Settings` di `app/core/config.py`.
- **Kompatibilitas Query Dialect Postgres Neon pada Repositori (`app/repositories/order_repo.py`, `app/repositories/report_repo.py`)**:
  - Memperbarui query filter enum (`Order.status`, `Order.created_via`, `Invoice.status`, `Payment.payment_status`) pada method `get_all_orders` / `get_seller_orders` dan `get_orders_by_customer_id` / `get_buyer_orders` agar selalu mengevaluasi `.value` atau string value, mencegah mismatch tipe data di PostgreSQL.
  - Menggunakan `cast(Order.created_via, String) == "chatbot"` untuk menjamin query analitik kebal terhadap type mismatch di dialect Postgres.
- **Resiliensi Schema Deserialisasi Pydantic (`app/schemas/order.py`)**:
  - Pada `OrderItemRead` (`OrderItemOut`), menambahkan validator `@field_validator('custom_decoration_charge', 'hpp_snapshot', mode='before')` dengan method `@classmethod def sanitize_null_floats(cls, v): return 0.0 if v is None else v` untuk menjamin nilai `None` / `NULL` dari database historis otomatis di-fallback ke `0.0`.
  - Pada `OrderRead` (`OrderOut`), menambahkan validator `@field_validator('created_via', mode='before')` dengan method `@classmethod def sanitize_created_via(cls, v): return "BUYER_SITE" if not v else v` untuk menangani field `created_via` bernilai `None` / `NULL` / string kosong.
- **Resiliensi Agregasi Finansial SQL & Python (`app/repositories/report_repo.py`, `app/services/report_service.py`)**:
  - Membungkus field `OrderItem.hpp_snapshot` dan `OrderItem.custom_decoration_charge` dengan `func.coalesce(OrderItem.hpp_snapshot, 0.0)` pada query total HPP serta query profitabilitas produk per item.
  - Menjamin iterasi Python pada breakdown profitabilitas kebal terhadap nilai `None` (`row.total_revenue or 0`, `row.total_hpp or 0`), mencegah unhandled `TypeError` pada kalkulasi margin laba.
- **Pencatatan Structured Error Log (`app/services/order_service.py`, `app/services/report_service.py`)**:
  - Menambahkan `logger.error(f"DETAIL ERROR: {repr(e)}", exc_info=True)` pada seluruh blok `except Exception as e:` di layer service untuk menjamin pesan error utuh dari database PostgreSQL tercatat lengkap saat terjadi kendala.
- **Automated Tests (`tests/test_financial_report.py`, `tests/test_seller_orders.py`, `tests/test_buyer_orders_payments.py`)**:
  - Menambahkan pengujian `test_financial_report_null_hpp_snapshot_resilience` pada `tests/test_financial_report.py` untuk memverifikasi kalkulasi laporan keuangan pada pesanan historis dengan `hpp_snapshot = NULL` dan `custom_decoration_charge = NULL`.
  - Seluruh 55 test suite pada Backend berjalan lancar dan lulus (**100% PASSED**).

### 001t. Perbaikan Root Cause HTTP 500 pada Endpoint Seller Orders (GET /orders)
- **Proteksi Perhitungan Piutang / Amount Due (`app/services/order_service.py`)**:
  - Memperbarui fungsi `_attach_payment_amounts` agar memeriksa keberadaan `order.invoice.total_tagihan is not None` sebelum melakukan konversi desimal `Decimal(str(order.invoice.total_tagihan))`.
  - Mencegah unhandled exception `decimal.InvalidOperation` jika terdapat invoice pesanan lama yang memiliki `total_tagihan = NULL`.
- **Penguatan Schema Deserialisasi Pydantic (`app/schemas/order.py`)**:
  - Menambahkan default fallback aman pada schema `CustomerOrderOut` (`nama: Optional[str] = "Customer"`, `nomor_wa: Optional[str] = ""`), `InvoiceOut` (`total_tagihan: Optional[Decimal] = Decimal("0.00")`), dan `OrderOut` (`metode_pengiriman = "pickup"`, `created_via = "web"`, `total_harga_pesanan = Decimal("0.00")`).
  - Mengeliminasi `pydantic_core.ValidationError` saat endpoint `GET /orders` memuat seluruh riwayat pesanan toko (all-time) yang memuat data historis.
- **Exception Shielding & Logging Komprehensif pada Seller Orders Router (`app/api/routes/order.py`)**:
  - Membungkus endpoint `GET /orders` (`list_seller_orders`) dan `GET /orders/{order_id}` (`get_seller_order_detail`) dalam blok `try-except Exception as e` dengan pencatatan structured error log.
- **Automated Tests (`tests/test_seller_orders.py`)**:
  - Menambahkan pengujian `Test 6`: Validasi pengambilan seluruh pesanan toko (`GET /orders`) dengan data item/invoice yang mengandung field `NULL`, memastikan status HTTP 200 OK berhasil dikembalikan dengan struktur nested data yang utuh.
  - Seluruh 53 unit & integration tests di test suite berjalan sukses (**100% PASSED**).

### 001s. Perbaikan Root Cause HTTP 500 pada Endpoint Riwayat Pesanan Buyer (GET /orders/buyer)
- **Isolasi Migrasi Enum PostgreSQL (`app/main.py`, `app/core/migrations.py`)**:
  - Memisahkan eksekusi `ensure_order_status_enum` ke koneksi *autocommit* terpisah (`async with engine.connect() as auto_conn:`) sebelum membuka blok transaksi DDL `async with engine.begin() as conn:`.
  - Mengeliminasi error PostgreSQL `ActiveSQLTransactionError` / `InFailedSQLTransactionError: ALTER TYPE ... ADD cannot run inside a transaction block` yang sebelumnya menyebabkan seluruh migrasi tabel & kolom sesudahnya (`ensure_payment_columns` dan `ensure_review_columns`) di-rollback.
  - Menjamin kolom `payments.settled_at` dan `payments.updated_at` berhasil terpasang di database PostgreSQL tanpa memicu `UndefinedColumnError` saat pemanggilan eager-loading relasi payments.
- **Resiliensi Deserialisasi Schema Pydantic (`app/schemas/order.py`)**:
  - Memperbarui fungsi helper `_round2(v, default=None)` dengan parameter nilai fallback.
  - Memperbarui schema `OrderItemOut` agar field `custom_decoration_charge`, `subtotal`, dan `hpp_snapshot` bertipe `Optional[Decimal] = Decimal("0.00")` dan validator `round_money` otomatis mengonversi nilai `None` (dari database atau data lama) menjadi `Decimal("0.00")`.
  - Memperbarui `OrderOut.total_harga_pesanan` dengan validator `round_total` agar kebal terhadap `None`.
  - Mengeliminasi crash deserialisasi `pydantic_core.ValidationError` pada pesanan kustom atau order dengan data historis.
- **Exception Shielding & Logging Komprehensif (`app/api/routes/order.py`)**:
  - Membungkus handler `GET /orders/buyer` dan `GET /orders/buyer/{id}` dengan blok `try-except Exception as e` dan *structured error logging*.
  - Mengembalikan status HTTP 500 terstruktur yang rapi dengan header CORS utuh jika terjadi kegagalan tak terduga.
- **Automated Tests (`tests/test_buyer_orders_payments.py`)**:
  - Menambahkan pengujian `Test 13`: Validasi deserialisasi pesanan buyer dengan kolom `OrderItem` bernilai `None` (`hpp_snapshot`, `custom_decoration_charge`) pada status `completed`, memastikan response berhasil dikembalikan dengan status HTTP 200 OK dan nilai default `0.00`.
  - Seluruh 53 unit & integration tests di test suite berjalan sukses (**100% PASSED**).

### 001r. Implementasi Kontrak Endpoint Frontend: Dashboard Summary, Manual Payment & Global Reviews
- **Dashboard Summary (`app/api/routes/report.py`, `app/schemas/report.py`, `app/services/report_service.py`, `app/repositories/report_repo.py`)**:
  - Memperbarui endpoint `GET /reports/summary`: Menghapus proteksi `require_service_key` (X-Service-Key) dan menggantikannya dengan otorisasi JWT untuk role OWNER, ADMIN, dan STAFF (`require_internal_user` / `require_staff_or_above`).
  - Menambahkan schema Pydantic `ReportSummary` dan `RecentOrderSummary` dengan struktur:
    - `total_products: int`: Total seluruh produk terdaftar di sistem.
    - `active_products: int`: Jumlah produk berstatus aktif (`is_active = True`).
    - `total_revenue: Decimal`: Total akumulasi penerimaan pembayaran sukses (`PaymentStatusEnum.success`).
    - `total_orders: int`: Total seluruh pesanan yang masuk di sistem.
    - `recent_orders: List[RecentOrderSummary]`: 5 pesanan terbaru lengkap dengan `id`, `customer_name` (dan alias `nama_customer`), `total_price` (dan alias `total_harga`/`total_harga_pesanan`), serta `status`.
  - Mendukung query parameter opsional `start_date` dan `end_date` (jika tidak disertakan, metrik dihitung secara akumulatif all-time).

- **Manual Payment (`app/api/routes/payment.py`, `app/schemas/payment.py`, `app/services/payment_service.py`, `app/models/payment.py`, `app/models/order.py`)**:
  - Menambahkan endpoint `POST /payments/manual` dengan request body schema `ManualPaymentRequest`:
    - `order_id: str` (mendukung string/integer)
    - `amount: float` (nominal pembayaran)
    - `payment_method: str` (contoh: `'CASH'`, `'TRANSFER'`)
    - `notes: Optional[str]` (catatan pembayaran kasir/admin)
  - Menambahkan response schema `ManualPaymentResponse` dengan status pembayaran `payment_status: "PAID"`.
  - Logika bisnis manual payment:
    - Memvalidasi keberadaan `Order` dan membuat `Invoice` jika belum tersedia.
    - Membuat record transaksi `Payment` baru dengan status `PaymentStatusEnum.success`, metode pembayaran yang dipilih, `settled_at = now()`, dan `notes`.
    - Memperbarui status `Invoice` menjadi `paid` (atau `partial` jika belum lunas).
    - Memperbarui status pesanan terkait dari `pending` menjadi `in_process` sesuai alur produksi dapur.
    - Menambahkan properti `payment_status` pada model `Order` dengan eager loading `lazy="selectin"` pada relasi invoice.
    - Mengirimkan sinyal webhook status pembayaran ke Chatbot (`notify_payment_status`) setelah database commit.

- **Global Reviews (`app/api/routes/review.py`, `app/schemas/review.py`, `app/services/review_service.py`, `app/repositories/review_repo.py`, `app/models/review.py`)**:
  - Menambahkan endpoint `GET /reviews/latest` dengan query parameter `limit` (default 6, type int, range 1-50).
  - Ditempatkan sebelum route berparameter `GET /{review_id}` untuk mencegah routing conflict pada FastAPI.
  - Mengambil ulasan terbaru secara global tanpa mewajibkan `product_id`.
  - Menerapkan eager loading (`selectinload(Review.product)`, `selectinload(Review.customer)`, `selectinload(Review.order)`) untuk mengeliminasi potensi `MissingGreenlet` lazy-loading error.
  - Menambahkan bidang langsung `product_name` dan `customer_name` pada response model `ReviewOut` dan model ORM `Review`.

- **Automated Tests (`tests/test_frontend_contracts.py`)**:
  - `test_dashboard_summary_contract`: Memverifikasi RBAC (401 unauthenticated, 403 buyer, 200 staff/admin/owner) dan validasi struktur data `ReportSummary`.
  - `test_manual_payment_contract`: Memverifikasi pencatatan pembayaran manual, transisi status pembayaran menjadi `PAID`, transisi order menjadi `in_process`, dan pencatatan record `Payment` di database.
  - `test_global_reviews_latest_contract`: Memverifikasi pengambilan review terbaru secara global dengan parameter `limit`, urutan kronologis terbaru, serta kelengkapan `product_name` dan `customer_name`.
  - Seluruh 53 unit tests di suite pengujian pytest berjalan sukses (**100% PASSED**).

### 001q. Stabilitas & Keamanan Endpoint Pembuatan Order Buyer (POST /orders/buyer)
- **Penanganan Duplicate Customer / Phone (Fix 409 Conflict) (`app/services/order_service.py`, `app/repositories/customer_repo.py`, `app/utils/phone.py`)**:
  - Menambahkan fungsi helper `get_phone_variants(phone)` pada `app/utils/phone.py` yang memproduksi kandidat format nomor telepon (`08xx`, `62xx`, `+62xx`, dll).
  - Memperbarui `customer_repo.get_by_nomor_wa` dan `customer_repo.upsert` untuk mencari data customer yang sudah ada menggunakan seluruh varian format nomor telepon sebelum memutuskan membuat record baru.
  - Memperbarui `get_or_create_customer_for_buyer` pada `app/services/order_service.py` untuk mengeliminasi potensi error `UNIQUE constraint failed: customers.nomor_wa` / HTTP 409 Conflict dengan langsung menggunakan record customer yang sudah ada.
  - Menyelaraskan verifikasi kepemilikan nomor telepon pada `app/services/review_service.py` (`update_review` & `delete_review`) agar tidak gagal saat membandingkan nomor format `08xx` vs `62xx`.
- **Unik ID Pesanan & Invoice (`app/services/order_service.py`, `app/services/payment_service.py`, `app/models/order.py`, `app/core/migrations.py`)**:
  - Mengubah pembuatan `nomor_invoice` pada `create_new_order` dan `create_custom_order` dengan format `INV-YYYYMMDD-{order_id}-{random_suffix}` menggunakan random hex suffix 6 karakter (`secrets.token_hex(3)`). Hal ini menjamin nomor invoice selalu unik dan tidak akan bentrok saat buyer melakukan order ulang.
  - Memperlebar tipe kolom `invoices.nomor_invoice` dari `VARCHAR(30)` menjadi `VARCHAR(50)` pada model `Invoice` dan migrasi PostgreSQL `ensure_order_columns`.
  - Memperbarui parameter `order_id_midtrans` pada `payment_service.create_midtrans_charge` dengan format `{nomor_invoice}-PAY-{timestamp_ms}-{suffix}` sehingga pembayaran ulang pesanan tidak memicu error duplicate transaction ID di Midtrans.
- **Exception Handler Terpusat & Anti-Crash CORS (`app/api/routes/order.py`, `app/services/order_service.py`)**:
  - Membungkus alur `POST /orders/buyer` (`create_order_for_buyer`) dalam blok `try-except Exception as e`.
  - Mengembalikan `HTTPException(status_code=400, detail=str(e))` lengkap dengan pesan error spesifik jika terjadi kesalahan data pesanan, sehingga backend tidak melempar crash HTTP 500 yang menutup header CORS.
  - Mengubah penanganan error `create_new_order` agar melempar HTTP 400 Bad Request alih-alih HTTP 500 Internal Server Error saat validasi data gagal.
- **Automated Tests (`tests/test_buyer_orders_payments.py`)**:
  - Menambahkan pengujian `Test 10`: Validasi penanganan duplicate customer dengan varian format nomor HP (`62xx` pre-existing vs `08xx` buyer), memastikan record customer digunakan kembali tanpa memicu HTTP 409 Conflict.
  - Menambahkan pengujian `Test 11`: Validasi keunikan `nomor_invoice` dengan suffix acak antarpesanan.
  - Menambahkan pengujian `Test 12`: Validasi penanganan error pada `POST /orders/buyer` dengan payload invalid yang mengembalikan status HTTP 4xx (400/422) dengan pesan `detail` spesifik tanpa crash HTTP 500.
  - Seluruh 50 unit tests di test suite berjalan lancar (**100% PASSED**).

### 001p. Fitur Review: Order Eligibility, Duplicate Protection & Composite Constraint
- **Model & Database Migration (`app/models/review.py`, `app/core/migrations.py`, `app/main.py`)**:
  - Menambahkan kolom `order_id` (Foreign Key ke `orders.id`, `nullable=False`, `index=True`) pada model `Review`.
  - Menambahkan composite Unique Constraint `uq_review_order_product_customer` untuk `(order_id, product_id, customer_id)` pada tabel `reviews`.
  - Menambahkan fungsi migrasi `ensure_review_columns` di `app/core/migrations.py` yang menambahkan kolom `order_id` jika belum ada dan membuat unique index `uq_review_order_product_customer` pada database PostgreSQL.
  - Memanggil `ensure_review_columns` di siklus hidup aplikasi (`lifespan`) pada `app/main.py`.
- **Status Pesanan Completed & State Machine (`app/models/order.py`, `app/core/state_machine.py`, `app/core/migrations.py`)**:
  - Menambahkan nilai status `completed = "completed"` pada enum `OrderStatusEnum`.
  - Memperbarui state machine transisi status pesanan (`ORDER_TRANSITIONS` & `ORDER_TERMINAL_STATES`) sehingga status `ready`, `delivered`, dan `picked_up` dapat bertransisi ke `completed`.
  - Menambahkan value `'completed'` pada PostgreSQL enum `orderstatusenum` via `ensure_order_status_enum`.
- **Perbaikan Request & Response Schema (`app/schemas/review.py`)**:
  - Memperbarui `ReviewCreate` agar wajib menerima `order_id: int`, `product_id: int`, `rating: int` (1-5), dan `comment: str` (dengan dukungan backward compatibility alias `komentar`).
  - Memperbarui `ReviewOut` dengan field `order_id`, `comment`, dan `komentar`.
  - Memperbarui `ReviewUpdate` dengan sanitasi teks dan sinkronisasi `comment`/`komentar`.
- **Validasi Business Logic & Duplicate Protection (`app/services/review_service.py`, `app/repositories/review_repo.py`)**:
  - Pada method `create_review`:
    1. **Order Completion Check**: Memastikan pesanan ada, milik buyer yang sedang login (`customer_id`), dan berstatus selesai (`completed`, `delivered`, atau `picked_up`). Jika tidak, melempar `HTTP 400 Bad Request` dengan detail `"Hanya pesanan yang sudah selesai yang dapat diulas."`.
    2. **Product in Order Check**: Memverifikasi `product_id` terdapat di dalam `order_items` dari pesanan tersebut. Jika tidak, melempar `HTTP 400 Bad Request` dengan detail `"Produk tidak terdapat dalam pesanan ini."`.
    3. **Duplicate Check**: Memverifikasi kombinasi `(order_id, product_id)` belum pernah diulas di database melalui query `review_repo.get_by_order_and_product`. Jika sudah ada, melempar `HTTP 400 Bad Request` dengan detail `"Anda sudah memberikan ulasan untuk produk pada pesanan ini."`.
  - Menambahkan eager loading `selectinload(Review.order)` pada repository query.
- **Automated Tests (`tests/test_review.py`)**:
  - Menambahkan 6 unit & integration tests baru:
    * `test_create_review_success_for_completed_order`: Memvalidasi keberhasilan pembuatan review untuk order completed dan rekalkulasi rating & review count produk.
    * `test_create_review_delivered_and_picked_up_statuses`: Memvalidasi bahwa status `delivered` dan `picked_up` juga memenuhi syarat order selesai.
    * `test_create_review_fails_when_order_not_completed`: Memvalidasi penolakan order yang masih in-process (400 "Hanya pesanan yang sudah selesai yang dapat diulas.").
    * `test_create_review_fails_when_product_not_in_order`: Memvalidasi penolakan jika produk tidak ada dalam item pesanan (400 "Produk tidak terdapat dalam pesanan ini.").
    * `test_create_review_duplicate_protection`: Memvalidasi penolakan review berulang pada produk dan order yang sama (400 "Anda sudah memberikan ulasan untuk produk pada pesanan ini.").
    * `test_create_review_fails_for_other_customer_order`: Memvalidasi penolakan jika buyer mencoba mereview pesanan milik customer lain.
  - Seluruh 50 unit tests di test suite berjalan lancar (**100% PASSED**).

### 001o. Token Expiry Window & Session Revocation (Logout Endpoint)
- **Konfigurasi Expiry Window Token (`app/core/config.py`, `app/core/security.py`, `.env`, `README.md`)**:
  - Mengatur masa berlaku JWT Access Token default menjadi **60 menit (1 jam)** via `ACCESS_TOKEN_EXPIRE_MINUTES = 60`.
  - Menambahkan property `ACCESS_TOKEN_EXPIRE_MINUTES` pada class `Settings` di `app/core/config.py` dan menyelaraskan konfigurasi di `.env` serta panduan `README.md`.
  - Memasukkan klaim unik `jti` (UUID v4) ke dalam setiap token yang di-generate via `create_access_token`.
- **Endpoint Revoke Session (`POST /auth/logout`) (`app/api/routes/auth.py`, `app/schemas/auth.py`)**:
  - Menambahkan endpoint terproteksi `POST /auth/logout` yang memerlukan header `Authorization: Bearer <token>`.
  - Mengembalikan schema `LogoutResponse` (`{"status": "ok", "message": "Successfully logged out"}`).
- **In-Memory Token Blacklist (`app/core/security.py`, `app/core/cache.py`)**:
  - Mengimplementasikan fungsi `revoke_token` yang mendaftarkan token ke `app_cache` (TTLCache) berdasarkan `jti`, signature token, dan token raw dengan TTL sisa masa kedaluwarsa (`remaining_ttl = exp - now`).
  - Mengintegrasikan pengecekan blacklist pada fungsi `decode_token`: setiap token yang sudah dicabut langsung ditolak dengan status `HTTP 401 Unauthorized` (`{"detail": "Token has been revoked"}`).
  - Melindungi seluruh endpoint terproteksi (`get_current_user_payload`, `get_current_user_id`, `get_current_buyer`, `get_auth_identity_optional_service_or_jwt`) dari penggunaan kembali token yang telah di-logout.
- **Automated Unit Tests (`tests/test_auth_logout.py`)**:
  - 3 unit tests baru:
    * `test_access_token_expire_minutes_configuration`: Memvalidasi `ACCESS_TOKEN_EXPIRE_MINUTES == 60` di settings dan modul security.
    * `test_token_creation_includes_jti_and_60m_expiry`: Memvalidasi kehadiran klaim `jti` dan masa berlaku 60 menit pada payload JWT.
    * `test_logout_endpoint_and_token_revocation`: Memvalidasi alur lengkap logout: token dicabut, ditolak saat akses ulang (401), penolakan logout berulang (401), dan token aktif lain tetap berfungsi normal.
  - Seluruh 44 unit tests di test suite berjalan lancar (**100% PASSED**).

### 001n. Financial Report Cash Flow, Historical Integrity & Refund Recognition
- **Integritas Historis `cash_received` (Opsi 2) (`app/repositories/report_repo.py`, `app/api/routes/report.py`)**:
  - Memastikan pembayaran yang berhasil diproses pada periode berjalan tetap dihitung ke dalam `cash_received` berdasarkan `Payment.created_at`, meskipun di kemudian hari (periode berikutnya) status pembayaran tersebut berubah menjadi `Refunded`.
  - Melindungi integritas data historis sehingga laporan arus kas masuk pada bulan-bulan lampau tidak terdistorsi atau berkurang akibat refund di masa mendatang.
- **Penambahan Field `cash_refunded` (`app/repositories/report_repo.py`, `app/schemas/report.py`, `app/api/routes/report.py`)**:
  - Mengimplementasikan query kalkulasi total pengembalian dana riil (`cash_refunded`) berdasarkan timestamp eksekusi refund (`coalesce(Payment.updated_at, Payment.created_at)`) yang jatuh dalam rentang periode filter.
  - Menambahkan kolom `updated_at` pada model `Payment` dan memperbarui `ensure_payment_columns` di `app/core/migrations.py`.
  - Mencatat timestamp `payment.updated_at = now()` saat webhook refund diproses atau saat seller/admin mengubah status pesanan menjadi `refunded`.
- **Perhitungan Arus Kas Bersih `net_cash_flow` (`app/repositories/report_repo.py`, `app/schemas/report.py`)**:
  - Mengkalkulasikan pergerakan kas bersih per periode: `net_cash_flow = cash_received - cash_refunded`.
  - Memasukkan `cash_refunded` dan `net_cash_flow` ke dalam `FinancialReportResponse`, `FinancialReportDetail`, dan `FinancialReportSummary`.
- **Automated Test Suite (`tests/test_financial_report.py`)**:
  - Memperbarui assertions pada seluruh unit test finansial untuk memvalidasi `cash_refunded` dan `net_cash_flow`.
  - Menambahkan unit test baru `test_cross_period_refund_and_historical_integrity`:
    * Memvalidasi pembayaran di bulan Januari tetap tercatat di `cash_received` Januari meskipun di-refund pada Februari.
    * Memvalidasi bulan Februari secara akurat mencatat `cash_refunded` dan `net_cash_flow = -150.000`.
    * Memvalidasi rekonsiliasi periode gabungan menghasilkan `net_cash_flow = 0.00`.
  - Seluruh 41 unit tests di test suite berjalan lancar (**100% PASSED**).

### 001m. Financial Report Cash Received, Cumulative Outstanding & Non-Refundable DP Logic
- **Penambahan Field `cash_received` (Cash Basis Flow) (`app/repositories/report_repo.py`, `app/schemas/report.py`)**:
  - Mengakumulasikan total riil nominal pembayaran sukses berdasarkan `Payment.created_at` yang jatuh dalam rentang filter tanggal (`start_date` s.d. `end_date`).
  - Mencerminkan pergerakan arus kas masuk aktual (*cash basis flow*) yang mencakup pembayaran DP, pelunasan order, maupun DP pesanan yang dibatalkan tanpa refund.
  - Memasukkan `cash_received` ke dalam response schema `FinancialReportResponse` dan `FinancialReportDetail`.
- **Perbaikan Logika `outstanding_payments` Kumulatif (`app/repositories/report_repo.py`)**:
  - Mengubah kalkulasi piutang pelanggan / sisa tagihan invoice menjadi kumulatif per titik akhir periode (`Order.created_at <= end_date`).
  - Menghitung SELURUH sisa tagihan dari invoice yang belum `paid` (status `unpaid` dan `partial`) pada order non-cancelled/non-refunded, termasuk pesanan dari periode-periode sebelumnya yang belum diselesaikan pembayarannya.
  - Membatasi pembayaran pengurang hanya pada pembayaran sukses yang terjadi hingga titik akhir periode (`Payment.created_at <= end_date`).
- **Penanganan DP Hangus (`non_refundable_dp_income`) (`app/repositories/report_repo.py`, `app/schemas/report.py`)**:
  - Mengidentifikasi pesanan berstatus `cancelled` yang memiliki pembayaran sukses (DP) yang tidak direfund.
  - Memasukkan nominal DP tersebut ke dalam `cash_received`.
  - Mencatat nominal ke dalam field `non_refundable_dp_income` (dan alias `other_income`) pada laporan keuangan.
  - Memperhitungkan DP hangus ke dalam laba bersih: `net_profit = gross_profit - expenses_total + non_refundable_dp_income`.
- **Pembaruan Pydantic Schema `FinancialReportResponse` (`app/schemas/report.py`)**:
  - Menyelaraskan nama field respon mencakup: `revenue`, `total_revenue`, `cash_received`, `hpp_total`, `total_hpp_cost`, `gross_profit`, `expenses_total`, `total_expenses`, `net_profit`, `outstanding_payments`, `non_refundable_dp_income`, `other_income`, `product_profitability`, `full_product_profitability`, dan `supplier_spending`.
  - Mengarahkan `FinancialReportResponse = FinancialReportDetail` untuk backward compatibility penuh.
  - Memperbarui `app/services/report_service.py` dan `app/api/routes/report.py` untuk menggunakan `FinancialReportResponse`.
- **Automated Test Suite (`tests/test_financial_report.py`)**:
  - Memperbarui assertions pada `test_financial_report_date_consistency_and_unpaid_exclusion` dan `test_partial_payment_and_cancelled_orders_handling` untuk memvalidasi `cash_received` dan field skema baru.
  - Menambahkan test baru `test_cumulative_outstanding_and_non_refundable_dp` untuk memvalidasi akumulasi piutang lintas periode, pengakuan DP hangus pada pesanan dibatalkan, dan rekonsiliasi `net_profit`.
  - Seluruh 40 tests di test suite berjalan lancar (**100% PASSED**).

### 001l. Stability, Security, and Performance Enhancements
- **Upload Size Limit (`app/main.py`)**:
  - Menambahkan middleware ASGI `MaxBodySizeMiddleware` yang membaca header `Content-Length`.
  - Me-reject request dengan ukuran body melebihi 5 MB (5,242,880 bytes) dengan response `HTTP 413 Payload Too Large`.
  - Melindungi server backend dari ancaman overload resource / DoS upload.
- **External HTTP Client Timeout Handling (`app/services/payment_service.py`, `app/utils/cloudinary_helper.py`)**:
  - Menambahkan parameter `timeout=10.0` detik secara eksplisit pada `httpx.AsyncClient` di `create_midtrans_charge` dan `refresh_if_pending`.
  - Menambahkan parameter `timeout=10` pada `cloudinary.uploader.upload` di `cloudinary_helper.py`.
  - Menghindari server blocking/hanging permanen jika gateway pembayaran atau service CDN mengalami degradasi jaringan.
- **In-Memory Caching Katalog & FAQ (`app/core/cache.py`, `app/api/routes/product.py`, `app/api/routes/faq.py`)**:
  - Mengimplementasikan class `TTLCache` murni Python dengan eviction berbasis `time.monotonic()` dan default TTL 300 detik (5 menit).
  - Menyediakan singleton `app_cache` dengan fungsionalitas `get`, `set`, `invalidate`, dan `invalidate_prefix`.
  - Menerapkan cache pada `GET /products/` (berbasis query parameter: `only_active`, `kategori`, `only_available`).
  - Menerapkan cache pada `GET /faq` (berbasis query parameter: `skip`, `limit`, `only_active`).
  - Menambahkan auto cache-invalidation saat ada operasi penambahan/pengubahan data produk (`POST /products/`, `PUT /products/{id}`, `DELETE /products/{id}`, `POST /products/{id}/image`, `PATCH /products/{id}/price`) atau FAQ (`POST /faq`, `PUT /faq/{id}`, `DELETE /faq/{id}`).
- **Uptime & Health Check Endpoint (`app/main.py`)**:
  - Menambahkan endpoint publik `GET /health` (`/api/health` jika via root_path) yang mengeksekusi `SELECT 1` pada database PostgreSQL/SQLite.
  - Mengembalikan `{"status": "ok", "database": "connected"}` (HTTP 200) atau `{"status": "error", "database": "disconnected"}` (HTTP 503).
- **Spending Cap / Transaction Limit Protection (`app/services/order_service.py`)**:
  - Menambahkan konstanta `MAX_ORDER_AMOUNT = Decimal("50000000")` (Rp 50.000.000).
  - Menerapkan validasi limit transaksi sebelum pengecekan stok pada `create_new_order` dan `create_custom_order`.
  - Menolak pembuatan pesanan yang nominalnya di atas batas maksimal dengan `HTTP 400 Bad Request`.
- **Automated Test Suite (`tests/test_stability_features.py`)**:
  - 9 unit tests baru mencakup: health check endpoint, upload size limit rejection (>5MB) & allowance (<=5MB), spending cap rejection (>50 juta) & allowance (<=50 juta), serta operasi dasar, expiry, prefix invalidation, dan single key invalidation pada TTLCache.
  - Seluruh test suite (39 tests) berstatus **PASSED**.

### 001k. Financial Report Accounting Fix, Date Basis Consistency & Purchase-to-Stock Integration
- **Konsistensi Basis Tanggal Laporan Keuangan (`app/repositories/report_repo.py`, `app/services/report_service.py`)**:
  - Mengubah kalkulasi Revenue dan HPP (Harga Pokok Penjualan) pada `GET /reports/financial` agar konsisten menggunakan tanggal penyelesaian pembayaran sukses (`settled_at` atau `created_at` dari pembayaran sukses).
  - Mengatasi masalah pergeseran periode (cross-month order): pesanan yang dibuat di akhir Januari namun baru dibayar pada Februari dialokasikan secara utuh ke bulan Februari (Revenue dan HPP sama-sama berada di Februari), menjaga akurasi *Gross Profit* dan *Net Profit*.
- **Eksklusi Pesanan Unpaid dari HPP & Profit (`app/repositories/report_repo.py`)**:
  - Menyaring query HPP agar hanya menghitung pesanan yang berstatus lunas (`InvoiceStatusEnum.paid` dan berstatus aktif non-cancelled/non-refunded).
  - Pesanan *unpaid* atau *pending payment* dikeluarkan secara total dari Revenue, HPP, Gross Profit, dan Net Profit.
- **Model Payment & Migrasi Database (`app/models/payment.py`, `app/core/migrations.py`, `app/main.py`)**:
  - Menambahkan kolom `settled_at TIMESTAMPTZ` pada model `Payment`.
  - Membuat fungsi migrasi `ensure_payment_columns` di `app/core/migrations.py` untuk menambahkan kolom pada PostgreSQL dan mem-backfill record yang sudah berstatus Success.
  - Memperbarui `_apply_transaction_status` di `app/services/payment_service.py` untuk otomatis mengisi `payment.settled_at = now()` saat pembayaran sukses.
- **Kelengkapan Response DTO Finansial (`app/schemas/report.py`)**:
  - Menambahkan field baru ke `FinancialReportDetail`:
    * `outstanding_payments: Decimal`: total piutang/pembayaran pending dari invoice aktif berstatus `unpaid` atau `partial`.
    * `full_product_profitability: List[ProductProfitabilityItem]`: rincian performa per item produk (qty terjual, revenue, HPP, gross profit, margin persentase).
    * `supplier_spending: List[SupplierSpendingItem]`: ringkasan total pengeluaran dan frekuensi PO per supplier.
- **Integrasi Purchase Order ke Inventory & Weighted Average Costing (`app/repositories/stock_repo.py`, `app/services/stock_service.py`, `app/services/purchasing_service.py`)**:
  - Mengembangkan otomasi saat PO ditandai diterima (`PUT /purchases/purchases/{purchase_id}` dengan `is_received = True`):
    * Menambahkan kuantitas barang diterima ke `stock_items.stok_tersedia`.
    * Menghitung ulang harga pokok rata-rata tertimbang (`stock_items.harga_per_satuan`) via formula Weighted Average Costing berpresisi Decimal.
    * Memicu rekalkulasi otomatis HPP produk resep terkait (`product_repo.calculate_and_update_product_price`).
    * Memproteksi status PO yang sudah diterima agar tidak dapat diubah kembali menjadi belum diterima (HTTP 409 Conflict) dan mencegah duplikasi penambahan stok.
- **Automated Test Suite (`tests/test_financial_report.py`, `tests/test_purchasing_stock_integration.py`)**:
  - `test_financial_report_date_consistency_and_unpaid_exclusion`: memvalidasi alokasi tanggal settlement, eksklusi order unpaid, kalkulasi gross profit & net profit, serta validitas field DTO baru.
  - `test_partial_payment_and_cancelled_orders_handling`: memvalidasi penanganan DP parsial dan eksklusi total pesanan cancelled/refunded dari outstanding payments.
  - `test_purchase_received_updates_stock_and_weighted_average_cost`: memvalidasi penambahan stok, kalkulasi Weighted Average Costing, dan tanggal diterima.
  - `test_purchase_receive_protections`: memvalidasi proteksi HTTP 409 pada un-receive dan proteksi stok dari double-counting.
  - `test_product_hpp_recalculation_on_purchase_received`: memvalidasi rekalkulasi otomatis HPP produk resep saat bahan baku baru diterima dengan harga berbeda.

### 001j. PostgreSQL Enum Migration Fix & Order Pending Preservation on Settlement
- **PostgreSQL Enum Migration (`app/core/migrations.py`, `app/main.py`)**:
  - Menambahkan fungsi migrasi async `ensure_order_status_enum(conn: AsyncConnection)` di `app/core/migrations.py`.
  - Menggunakan eksekusi autocommit `conn.execution_options(isolation_level="AUTOCOMMIT")` untuk menjalankan statement DDL PostgreSQL:
    `ALTER TYPE orderstatusenum ADD VALUE IF NOT EXISTS 'refunded';`
    Hal ini mencegah error PostgreSQL `25001: ALTER TYPE ... ADD cannot run inside a transaction block`.
  - Menyediakan fallback aman yang membuka koneksi mandiri jika connection sedang berada dalam transaction block.
  - Memanggil `ensure_order_status_enum` di `app/main.py` saat startup aplikasi dalam siklus `lifespan`.
- **Preservasi Status Pesanan `pending` saat Pelunasan (`app/services/payment_service.py`)**:
  - Menghapus blok transisi otomatis yang mengubah `order.status` menjadi `OrderStatusEnum.in_process` saat pembayaran lunas/settlement (`total_success >= invoice.total_tagihan`).
  - Menjaga status invoice ter-update menjadi `InvoiceStatusEnum.paid`, namun membiarkan `order.status` tetap berada di `OrderStatusEnum.pending`.
  - Hal ini menjamin pembeli yang telah melunasi pesanan tetap dapat membatalkan dan mengajukan refund sebelum admin toko secara manual memulai proses produksi di dapur (status `in_process`).
- **Automated Test Suite (`tests/test_settlement_and_migrations.py`)**:
  - Memvalidasi fungsi migrasi `ensure_order_status_enum` pada dialect non-PostgreSQL (skip) dan PostgreSQL (autocommit DDL).
  - Memvalidasi alur pelunasan pembayaran via `_apply_transaction_status`, memastikan `invoice.status == 'paid'` sementara `order.status == 'pending'`.

### 001i. Order Invoice PDF Generation & Download Endpoint (`GET /orders/{id}/invoice/pdf`)
- **Dependency & Utilitas PDF Generator (`requirements.txt`, `app/utils/pdf_generator.py`)**:
  - Menambahkan dependency `reportlab==5.0.1` pada `requirements.txt`.
  - Mengembangkan modul `app/utils/pdf_generator.py` dengan fungsi `generate_order_invoice_pdf(order: Order) -> io.BytesIO` yang menghasilkan file PDF Invoice pesanan A4 berdesain profesional dan rapi:
    * **Header Brand**: Nama "TOTI CAKERY", tagline toko, nomor invoice, tanggal order, dan badge status pembayaran (LUNAS / SETTLEMENT, DP / SEBAGIAN, BELUM LUNAS, REFUNDED).
    * **Informasi Pelanggan & Pengiriman**: Nama customer, nomor WhatsApp/kontak, alamat pengiriman, metode pengiriman (Pickup / Delivery), estimasi due date, dan status pesanan.
    * **Tabel Rincian Pesanan**: Kolom nomor urut, deskripsi nama produk/item (termasuk penanda biaya custom/dekorasi), jumlah (qty), harga satuan, dan subtotal dengan layout tabel zebra-striped.
    * **Ringkasan Tagihan & Catatan**: Catatan pesanan, total tagihan, total jumlah yang sudah terbayar (`amount_paid`), dan sisa tagihan (`amount_due`).
    * **Riwayat Transaksi Pembayaran**: Tabel rincian transaksi pembayaran (waktu transaksi, tipe DP/Final, metode pembayaran, status transaksi, nominal).
    * **Footer**: Ucapan terima kasih dan keterangan keabsahan dokumen invoice resmi.
- **FastAPI Endpoint Baru (`app/api/routes/order.py`)**:
  - Menambahkan endpoint `GET /orders/{id}/invoice/pdf`:
    * Menggunakan autentikasi gabungan `get_auth_identity_optional_service_or_jwt`.
    * Memvalidasi otorisasi: Buyer hanya diizinkan mengunduh invoice pesanannya sendiri (pencocokan nomor WhatsApp pelanggan dengan akun login buyer -> HTTP 404 jika tidak cocok agar tidak terjadi enumerasi).
    * Mengizinkan pengguna internal Seller (Staff dengan level 3, Admin dengan level 2, Owner dengan level 1).
    * Mengembalikan HTTP 404 jika order tidak ditemukan di database.
    * Eager-loading seluruh relasi terkait: `customer`, `order_items` beserta `product`, serta `invoice` beserta `payments`.
    * Mengembalikan `StreamingResponse` dengan header `Content-Disposition: attachment; filename="Invoice-TotiCakery-{order_id}.pdf"` dan `media_type="application/pdf"`.
- **Pengujian Terintegrasi & Verifikasi Unit Test (`tests/test_order_invoice_pdf.py`)**:
  - Menguji alur lengkap unduh PDF oleh Buyer pemilik pesanan (HTTP 200, validitas magic bytes `%PDF-`, header filename).
  - Menguji akses oleh seluruh role internal (Staff, Admin, Owner) -> HTTP 200.
  - Menguji pencegahan akses oleh Buyer lain terhadap pesanan yang bukan miliknya -> HTTP 404.
  - Menguji request tanpa autentikasi -> HTTP 401.
  - Menguji penolakan peran tidak berhak (level > 3) -> HTTP 403.
  - Menguji pesanan yang tidak terdaftar -> HTTP 404.

### 001h. State Machine Adjustment (`cancelled -> refunded`) & Dual-Signal Refund Webhooks ke Chatbot
- **State Machine & Enum Order Baru (`app/models/order.py`, `app/core/state_machine.py`)**:
  - Menambahkan nilai status baru `refunded = "refunded"` pada enum `OrderStatusEnum`.
  - Memperbarui `ORDER_TRANSITIONS` agar status `cancelled` diperbolehkan bertransisi ke status `refunded` (`OrderStatusEnum.cancelled: {OrderStatusEnum.refunded}`), serta mengizinkan transisi langsung dari status aktif (`pending`, `in_process`, `ready`) ke `refunded`.
  - Menetapkan `OrderStatusEnum.refunded` sebagai terminal state baru (`ORDER_TERMINAL_STATES = {delivered, picked_up, refunded}`). Status `cancelled` tidak lagi terminal karena dapat bertransisi ke `refunded`.
- **Manajemen Transisi Status & Rollback Stok (`app/services/order_service.py`)**:
  - Pada `update_order_status()`, jika status diubah menjadi `refunded` (misalnya oleh Admin via `PATCH /orders/{id}/status`), sistem memvalidasi transisi state machine, mengembalikan stok jika sebelumnya belum pernah dibatalkan (`_rollback_order_stock`), serta memperbarui status `invoice` dan `payment` terkait menjadi `refunded`.
  - Transaksi disimpan dengan `await db.commit()` terlebih dahulu sebelum mengeksekusi webhook notifikasi.
- **Webhook Notification Trigger 2 Sinyal ke Chatbot (`app/services/payment_service.py`, `app/services/order_service.py`, `app/services/chatbot_notify.py`)**:
  - Webhook internal ke Chatbot (`POST {CHATBOT_URL}/webhook/internal/orders/{order_id}/refunded` dengan header `X-Internal-Key: <CHATBOT_INTERNAL_KEY>`) kini ditembakkan secara presisi pada DUA kondisi kejadian pasca-commit (`commit=True`):
    1. **Sinyal Auto Refund (Poin 2.a)**:
       - Dipicu di `process_refund()` saat pemanggilan API Direct Refund Midtrans sukses (`refund_mode == "auto"`).
       - Status pesanan diset menjadi `refunded`.
       - Webhook langsung ditembakkan setelah `db.commit()`.
    2. **Sinyal Manual Refund Completed (Poin 2.b)**:
       - Saat refund otomatis tidak didukung / gagal (misal VA / QRIS HTTP 412), transaksi dicatat sebagai `refund_mode == "manual"` dan status awal pesanan diset ke `cancelled` (tanpa menembak webhook refund prematur ke Chatbot).
       - Saat Admin/Seller menyelesaikan transfer manual offline dan menandai pesanan menjadi `refunded` di Dashboard Site (melalui `PATCH /orders/{id}/status` atau `POST /orders/{id}/refund`), status pesanan berubah menjadi `refunded`, database di-commit, dan Sinyal 2.b ditembakkan ke Chatbot.
- **Pengujian & Verifikasi Terintegrasi**:
  - `tests/test_hardening.py`: Memvalidasi transisi `cancelled -> refunded` diizinkan dan `is_order_terminal(refunded)` benar.
  - `tests/test_refund.py`: Memverifikasi mode auto dan manual refund dengan status akhir `refunded`.
  - `tests/test_chatbot_refund_webhook.py`: Memverifikasi kedua sinyal pemicu webhook: Sinyal 2.a (Auto refund QRIS/Midtrans) dan Sinyal 2.b (Admin mark as refunded via PATCH status atau POST refund).

### 001g. User Service Eager Loading & Fix MissingGreenlet (POST /users & User Queries)
- **Eager Loading Relasi Role & Proteksi `role_name` (`app/models/user.py`)**:
  - Menetapkan konfigurasi `lazy="selectin"` pada relasi `role = relationship("Role", lazy="selectin")` di model `User`, sehingga setiap kali model `User` dimuat oleh SQLAlchemy secara asinkron, data relasi `Role` otomatis di-eager load tanpa memicu eksekusi IO sinkron tersembunyi.
  - Memperbarui property `role_name` dengan pemeriksaan status `inspect(self).unloaded`. Jika relasi `role` belum dimuat atau dalam status unattached/detached, property secara aman mengembalikan `""` (empty string) alih-alih mencoba melakukan lazy loading sinkron yang memicu fatal error `sqlalchemy.exc.MissingGreenlet`.
- **Eager Loading pada Repository & Service User (`app/repositories/user_repo.py`, `app/services/user_service.py`)**:
  - Pada `app/repositories/user_repo.py`, seluruh query pengguna (`get_user_by_username`, `get_user_by_id`, `get_user_role_level`, `get_takeover_handlers`, `get_user_by_email`, `get_user_by_phone`, `get_all_users`) kini secara konsisten menggunakan opsi eager loading `selectinload(User.role)`.
  - Fungsi `update_avatar_url` kini me-reload user dengan `selectinload(User.role)` setelah `await db.commit()`.
  - Pada `app/services/user_service.py`, fungsi `create_user()` kini mengaitkan instance `new_user.role = role_obj` secara eksplisit dan melakukan re-fetch user lengkap dengan `options(selectinload(User.role))` setelah `await db.commit()`. Hal ini menjamin saat FastAPI mereturn response Pydantic `UserOut.model_validate(user)`, atribut `role_name` dan `role` sudah siap terbaca tanpa error 500.
  - Perbaikan re-query serupa juga diterapkan pada mutasi user lainnya (`bootstrap_owner`, `update_user_profile`, `admin_update_user`, `deactivate_user`). Pada `admin_update_user`, pengubahan `role` atau `role_id` langsung mengikat objek role baru (`user.role = role_obj`) sehingga serialisasi response konsisten seketika.
- **Automated Test Suite Baru (`tests/test_user_management.py`)**:
  - Verifikasi pembuatan user baru via `POST /users` (Owner) untuk Admin (`role_id=2`) dan Staff (`role="staff"`) menghasilkan HTTP 201 dengan `role_name` & `role` terisi lengkap tanpa melempar HTTP 500 `MissingGreenlet`.
  - Verifikasi `GET /users`, `GET /users/me`, `PUT /users/me`, `PUT /users/{id}`, dan `PATCH /users/{id}/deactivate`.
  - Verifikasi eksekusi direct service `create_user` dalam session terisolasi untuk mereplikasi siklus request asinkron murni.

### 001f. Post-Commit Webhook Notifications & Refund Mode Indicator (Auto vs Manual 412 Handling)
- **Urutan Pemanggilan Webhook Notification (Post-Commit Execution) (`app/services/payment_service.py`, `app/services/chatbot_notify.py`)**:
  - Memastikan panggilan webhook notifikasi keluar ke Chatbot (`notify_payment_status` / `notify_refund_status`) **DIJAMIN** dieksekusi **SETELAH `db.commit()`** berhasil dilakukan di database backend.
  - Memperbarui fungsi `_apply_transaction_status` dengan parameter `commit: bool = False` yang mengumpulkan daftar event tertunda (`events_to_notify`). Jika `commit=True`, fungsi akan mengeksekusi `await db.commit()` terlebih dahulu sebelum menembak notifikasi HTTP ke Chatbot.
  - Mencegah potensi *race condition* di mana Chatbot yang menerima webhook secara instan melakukan query `GET /payments/{id}/status` atau `/orders/{id}` namun mendapati status transaksi belum ter-update karena database masih dalam status *uncommitted*.
- **Penanda `refund_mode` & Penanganan Direct Midtrans Refund Failure (VA / QRIS HTTP 412) (`app/services/payment_service.py`, `app/services/order_service.py`, `app/schemas/order.py`, `app/api/routes/order.py`)**:
  - Transaksi berbasis Virtual Account (BCA VA, BNI VA, dll.) dan QRIS tidak mendukung Direct Refund via API Midtrans (Midtrans mengembalikan respons HTTP 412 *Precondition Failed*).
  - Memperbarui fungsi `process_refund()` agar menangani HTTP 412, body status `412`, non-2xx status, atau error koneksi secara elegan tanpa melempar exception fatal, dan otomatis jatuh ke mekanisme *manual refund* (pembukuan database backend tetap diselesaikan).
  - Menambahkan schema baru `RefundResponse` pada `app/schemas/order.py` dan memperbarui respons endpoint `POST /orders/{order_id}/refund`:
    ```json
    {
      "message": "Order refund processed successfully",
      "order_id": 57,
      "status": "cancelled",
      "payment_status": "refunded",
      "refund_mode": "manual"
    }
    ```
    - `"refund_mode": "auto"`: Panggilan API Direct Refund ke Midtrans berhasil (status 200/201).
    - `"refund_mode": "manual"`: Panggilan API Midtrans gagal (error HTTP 412 untuk VA/QRIS, gateway error, atau pencatatan refund manual admin).
- **Pengujian & Verifikasi Terintegrasi**:
  - Diperbarui pada `tests/test_refund.py`: Pengujian alur refund sukses (`refund_mode: "auto"`) serta skenario uji khusus Midtrans HTTP 412 (VA method) yang memverifikasi fallback ke `refund_mode: "manual"`.
  - Diperbarui pada `tests/test_chatbot_refund_webhook.py`: Verifikasi response payload `RefundResponse` lengkap dan pemicu webhook notifikasi post-commit.

### 001e. Chatbot Webhook Triggers & Service-to-Service Refund
- **Sentralisasi Notifikasi Webhook Chatbot (`app/services/chatbot_notify.py`, `app/services/payment_service.py`, `app/services/order_service.py`)**:
  - Disediakan helper asinkron `notify_chatbot_order_event(order_id: int, event: str)` yang mengirimkan HTTP POST non-blocking (fire-and-forget) ke endpoint Chatbot internal dengan header `X-Internal-Key: <CHATBOT_INTERNAL_KEY>`.
  - Dilengkapi isolasi total `try-except Exception` dan timeout 5 detik sehingga kegagalan jaringan atau respons error dari service Chatbot tidak menggagalkan transaksi database di Backend.
  - **Pemicu Event Baru**:
    - `POST {CHATBOT_URL}/webhook/internal/orders/{order_id}/paid`: Memicu notifikasi saat transaksi pembayaran mencapai status `Success` (baik settlement DP maupun pelunasan final) di `_apply_transaction_status()`.
    - `POST {CHATBOT_URL}/webhook/internal/orders/{order_id}/refunded`: Memicu notifikasi saat pesanan di-refund melalui `cancel_and_refund_order()` atau saat menerima webhook refund dari Midtrans.
    - `POST {CHATBOT_URL}/webhook/internal/orders/{order_id}/ready`: Memicu notifikasi saat status pesanan diubah ke `ready`.
- **Service-to-Service Refund dengan Dual Authentication (`app/api/routes/order.py`, `app/services/order_service.py`, `app/schemas/order.py`)**:
  - Endpoint `POST /orders/{order_id}/refund` kini mendukung dua mekanisme otentikasi menggunakan dependency `get_auth_identity_optional_service_or_jwt`:
    1. **JWT Bearer Token** (Seller Internal: Owner, Admin, Staff).
    2. **Pre-Shared Service Key** via header `X-Service-Key` (Chatbot service).
  - Skema `RefundRequest` diperluas dengan field opsional `nomor_wa: Optional[str] = None`.
  - **Ownership Verification (Guard Kepemilikan WA)**: Untuk pemanggilan via service key, `nomor_wa` dari payload dicocokkan dengan nomor telepon customer pesanan menggunakan `normalize_phone_number()`. Mengembalikan `HTTP 403 Forbidden` jika nomor tidak cocok atau tidak disertakan.
  - **Strict Status Check (Guard Status Pesanan)**: Untuk pemanggilan via service key, proses refund **hanya diizinkan** jika pesanan masih berstatus `pending`. Mengembalikan `HTTP 400 Bad Request` jika pesanan sudah diproses (`in_process`, `ready`, dll.).
  - Fleksibilitas refund manual oleh Admin/Staff via token JWT tetap dipertahankan.
- **Normalisasi Nomor Telepon (`app/utils/phone.py`)**:
  - Ditambahkan alias `normalize_phone_number = normalize_phone` untuk konsistensi antar-service.
- **Automated Test Suite Baru (`tests/test_chatbot_refund_webhook.py`)**:
  - Verifikasi penolakan Chatbot refund saat `nomor_wa` salah atau tidak ada (403 Forbidden).
  - Verifikasi penolakan Chatbot refund pada order berstatus `in_process` (400 Bad Request).
  - Verifikasi keberhasilan Chatbot refund pada order berstatus `pending` (stok pulih, invoice & payment refunded, order cancelled).
  - Verifikasi fleksibilitas Admin refund via JWT.
  - Verifikasi pemicu webhook `/paid` dan `/refunded` ke Chatbot service.

### 001d. Frontend ↔ Backend Integration Fixes (Order & Seller Settings)
- **Penyesuaian RBAC Order Seller (`app/api/routes/order.py`, `app/api/dependencies.py`)**:
  - Mengubah dependency guard pada endpoint manajemen order seller (`GET /orders`, `POST /orders/custom`, `GET /orders/{order_id}`, `PATCH /orders/{order_id}/status`, `POST /orders/{order_id}/refund`) dari `require_admin_or_owner` menjadi `require_internal_user` (level 3).
  - Role **Staff** kini diizinkan penuh mengelola pesanan seller dan tidak lagi menerima error HTTP 403 Forbidden.
- **Product Name di OrderItem Schema (`app/schemas/order.py`, `app/models/order.py`, `app/models/product.py`, `app/services/order_service.py`)**:
  - Menambahkan field `product_name: Optional[str] = None` pada schema `OrderItemOut` beserta alias `OrderItemRead` & `OrderItemResponse`.
  - Menambahkan properti `name` pada model `Product` dan properti `product_name` dengan getter/setter pada model `OrderItem`.
  - Memastikan seluruh order item pada respons diperkaya dengan nama produk dari relasi database (`item.product.nama_produk` untuk master product atau `item.custom_product_name` untuk custom order) sehingga Frontend tidak lagi fallback ke 'Product #ID'.
- **Hardening Exception Polling Payment (`app/api/routes/payment.py`, `app/api/dependencies.py`)**:
  - Endpoint `GET /payments/{order_id}/status` kini melakukan validasi eksistensi order lebih awal (mengembalikan 404 jika tidak ditemukan).
  - Membungkus seluruh alur pemeriksaan status dalam `try-except` spesifik untuk menangani `HTTPException`, `SQLAlchemyError` (500), `AttributeError` (500), dan `Exception` tak terduga.
  - Memperbaiki dependency `get_auth_identity_optional_service_or_jwt` agar error internal database melempar status 500, bukan 401. Memastikan Frontend tidak keliru menganggap sesi login kedaluwarsa saat terjadi kendala internal.
- **Modul Seller Settings & User Management Endpoints (`app/api/routes/user.py`, `app/schemas/user.py`, `app/repositories/user_repo.py`, `app/services/user_service.py`)**:
  - `GET /users/me`: Mengambil data profil user seller internal yang sedang login (dilengkapi field `role_name` dan `role`).
  - `PUT/PATCH /users/me`: Update profil user yang sedang login (`username`, `email`, `phone_number`, `nomor_wa_admin`) dengan validasi keunikan.
  - `POST /users/me/change-password`: Ubah password user yang sedang login dengan memverifikasi `old_password` menggunakan hashing Bcrypt.
  - `GET /users`: List seluruh akun internal (Owner, Admin, Staff). Diproteksi dengan RBAC `require_owner` (Admin & Staff menerima 403 Forbidden).
  - `PUT/PATCH /users/{user_id}`: Edit data akun pengguna internal lain oleh Owner. Dilengkapi guard pencegahan Owner menonaktifkan akun sendiri.
  - `PATCH /users/{user_id}/deactivate`: Deaktivasi akun pengguna internal (`is_active = False`) oleh Owner.
  - `DELETE /users/{user_id}`: Hapus/deaktivasi akun pengguna internal oleh Owner dengan penanganan aman terhadap relasi foreign key transaksi.
- **Automated Test Suite Baru**:
  - `tests/test_order_settings_audit.py`: Menguji seluruh perbaikan integrasi secara komprehensif (Staff order RBAC, OrderItem product_name, payment polling 401 handling, profil user, password change, dan Owner RBAC guard).
  - Penyesuaian assertion pada `tests/test_seller_orders.py` agar mengonfirmasi akses Staff (200 OK).

### 001c. Fix CI Integration Tests & Test Data Isolation
- **Test Database Isolation (`test_owner_numbers.py` & `test_refund.py`)**:
  - Memperbaiki isu di mana status `dependency_overrides` bocor atau terhapus oleh test lain saat dieksekusi bersamaan oleh `pytest`.
  - Mengubah cara inisialisasi `app.dependency_overrides` agar diletakkan tepat sebelum pemanggilan `httpx.AsyncClient` di dalam blok tes untuk menjamin test tidak tanpa sengaja mengenai Database PostgreSQL asli milik CI yang menyebabkan AssertionError (`['628111111111']`).
  - Mengganti `TestClient` FastAPI yang sinkron menjadi asinkron `httpx.AsyncClient` ber-transport ASGI pada test file `test_owner_numbers.py`.
- **Memory Leak & Un-awaited Coroutines**:
  - Mengisolasi inisialisasi SQLAlchemy `AsyncEngine` (khusus SQLite memori dengan `StaticPool`) ke dalam _function-scope_ per fungsi tes untuk menghindari terbaginya koneksi pool antar _event-loop_ berbeda yang dibuat oleh `pytest-asyncio`.
  - Menutup/mematikan koneksi secara sadar di akhir setiap blok tes (`await test_engine.dispose()`) sehingga membersihkan RuntimeWarning *Connection._cancel was never awaited* saat proses *garbage collection*.

### 001b. Fitur Refund DP (Down Payment)
- **Refund Endpoint (`POST /orders/{order_id}/refund`)**:
  - Menambahkan endpoint khusus untuk melakukan proses *refund* pesanan yang telah dibayar DP/Lunas.
  - Dilindungi otentikasi Admin/Owner dan akan otomatis memvalidasi kondisi State Machine (hanya pesanan aktif dengan invoice `partial` atau `paid`).
  - Mengembalikan/rollback persediaan stok (StockItem) bahan baku sesuai resep.
- **Integrasi Midtrans Refund API**:
  - Mengirim HTTP Request ke API Midtrans `/v2/{order_id}/refund` untuk mengembalikan dana secara instan.
  - Terdapat mekanisme fallback: jika gateway menolak karena keterbatasan metode pembayaran (seperti Bank Transfer VA), sistem akan mencatatnya sebagai *refund manual* di database.
- **Webhook Update**:
  - Event webhook Midtrans (`transaction_status` = `refund` atau `partial_refund`) kini mem-bypass Idempotency guard jika status awal adalah `Success`.
  - Otomatis melakukan *cascading update* status Payment menjadi `Refunded`, Invoice menjadi `Refunded`, dan Order induk menjadi `Cancelled`.
- **Unit Test Baru**:
  - `test_refund.py` untuk menguji *Refund Flow* (Mock HTTPX Client) dan Webhook Refund.

### 001a. Endpoint Owner WA Numbers
- **Owner Numbers Endpoint (`GET /users/owner-numbers`)**:
  - Menambahkan endpoint untuk digunakan oleh Chatbot service (`service-to-service`).
  - Endpoint dilindungi dengan `verify_service_key` (`X-Service-Key` header).
  - Mereturn daftar nomor WhatsApp dari pengguna dengan Role Owner (Level 1) yang sedang aktif, dan sudah dinormalisasi menjadi standar E.164 (tanpa '+', mulai '62').

### 001. Input Hardening, XSS Prevention, & Bug Fixes (BE1-BE4)
- **User/Seller Creation Bugfix (`POST /users/`)**:
  - Memperbaiki isu "user hilang setelah di-refresh/tidak bisa login" dengan menambahkan mapping *alias* pada skema `UserCreate` (mendukung parameter `nama_lengkap`, `nomor_wa`, `role` string dari *payload* FE).
  - Mengubah logika validasi *Role* agar mencari langsung ke tabel `roles` di database dengan metode yang _case-insensitive_ (`func.lower(Role.nama_role)`) alih-alih _hardcoded_ dictionary.
  - Menyempurnakan _error handling_ pada `create_user` dengan menambahkan log print di *router* level (`app/api/routes/user.py`) agar error dari database PostgreSQL dapat terbaca di *log* Vercel/terminal, dan mengecek duplikasi `email` serta `phone_number`.
- **Schema Hardening & XSS Prevention (Seluruh `app/schemas/`)**:
  - **`app/utils/sanitize.py`**: Ditambahkan utilitas sanitasi teks global yang mendeteksi dan menolak tag HTML/script (seperti `<script>`, `javascript:`, `<iframe>`) untuk mencegah serangan XSS.
  - **Panjang Karakter Ketat**: Membatasi panjang `username` (maks 25, hanya alfanumerik/underscore/hyphen), `phone_number` / `nomor_wa` (maks 16), `email` (maks 100), serta `notes` / `alamat` / `customer_name` (maks 100 - 500 karakter).
  - Sanitizer diterapkan via `@field_validator` di `auth.py`, `user.py`, `customer.py`, `order.py`, dan `review.py`.
- **Customer Takeover (BE1)**:
  - `POST /api/customers/{nomor_wa}/takeover` kini melakukan **UPSERT**. Jika nomor WA belum terdaftar di tabel `customers`, sistem akan otomatis membuat record baru (nama placeholder "Customer") lalu mengaktifkan status takeover, bukan lagi mengembalikan HTTP 404.
- **Midtrans Rejection & Payment 201 Handling (BE2)**:
  - `POST /api/payments` sekarang memvalidasi `status_code` dari body JSON respons Midtrans (misalnya mendeteksi `"406"`). Jika Midtrans menolak, transaksi **tidak akan disimpan** menggantung di database dan endpoint mengembalikan HTTP 400.
  - Ditambahkan perlindungan idempotensi: jika sudah ada payment `pending` aktif, sistem mengembalikan data payment tersebut.
- **Minimum Order Validation (BE3)**:
  - Memperbaiki *guard condition* pada saat pembuatan order dari `if product.minimum_order and ...` menjadi `if product.minimum_order is not None and ...` untuk menghindari *bypass* validasi pada kondisi nilai minimum 0.
- **WhatsApp Phone Normalization (BE4)**:
  - Fungsi seed awal di `app/core/database.py` (`ensure_user`) sekarang menerapkan normalisasi `normalize_phone` sebelum masuk ke database. Format `08...` akan otomatis dikonversi ke format E.164 (`628...`).

### 000. High Concurrency, Race Condition, Security & Settlement Hardening
- **Payment & Order State Machine (`app/core/state_machine.py`)**:
  - Validasi transisi status satu arah (`is_valid_payment_transition` & `is_valid_order_transition`).
  - Mencegah backward rollback (misal: webhook `pending` stale menimpa status `Success` menjadi `Pending`).
  - Mendeteksi terminal state (`Success`, `Failed`, `Refunded` untuk payment; `Delivered`, `Picked Up`, `Cancelled` untuk order).
- **Payment Webhook Hardening & Idempotency (`app/services/payment_service.py`)**:
  - **Row-Level Locking (`SELECT ... FOR UPDATE`)**: Mengunci baris `Payment` saat memproses callback Midtrans untuk mencegah race condition double-settlement saat dua webhook masuk bersamaan.
  - **Idempotency Guard**: Jika transaksi sudah berada pada status terminal (`is_payment_terminal`), webhook duplikat langsung di-skip secara aman tanpa mutasi ganda ke Invoice/Order.
  - **Structured Audit Logging**: Setiap charge creation, state transition, blocked rollback, auto-transition order, dan pembaruan invoice dicatat dalam format terstruktur `[PAYMENT_AUDIT]`.
- **Inventory Concurrency & Auto-Retry Mechanism (`app/services/order_service.py`)**:
  - **Optimistic Locking Auto-Retry**: Menambahkan loop retry hingga 3 kali (`MAX_STOCK_RETRY = 3`) dengan re-fetch data bahan baku terbaru saat terjadi benturan versi (`StockItem.version`) di tengah concurrent checkout.
  - **Pessimistic Row Lock on Status & Cancellation**: Menambahkan `with_for_update()` pada pembaruan status order dan pembatalan pesanan (`cancel_order_by_customer`) untuk mencegah double-cancellation dan duplicate stock restoration.
  - Validasi state machine pada perubahan status order admin/seller.
- **In-Memory Rate Limiting & Anti-Spam Protection (`app/core/rate_limiter.py`, `app/main.py`)**:
  - Mengintegrasikan library `slowapi` berbasis memory (`memory://`) tanpa dependensi Redis tambahan.
  - Menetapkan limit ketat:
    - `POST /orders`, `/orders/buyer`, `/orders/custom`: `5/minute` per IP
    - `POST /payments`: `5/minute` per IP
    - `POST /auth/login`, `/auth/buyer/register`, `/auth/buyer/login`: `10/minute` per IP (anti brute force)
    - `POST /auth/verify/wa/start`: `6/minute` per IP (anti-spam OTP)
    - `POST /payments/notify`: `30/minute` rate limiter
  - Global exception handler `RateLimitExceeded` menghasilkan respon HTTP 429 Too Many Requests yang standar.
- **Test Suite Reorganization & Pytest Integration (`tests/`, `pytest.ini`)**:
  - Seluruh berkas pengujian (`test_avatar_upload.py`, `test_hardening.py`, `test_master_data.py`, `test_seller_orders.py`, `test_buyer_orders_payments.py`, `test_imports.py`) dipindahkan dari direktori `app/` ke root direktori `tests/`.
  - Dikonfigurasikan `pytest.ini` (`asyncio_mode = auto`, `pythonpath = .`, `testpaths = tests`) dan `tests/conftest.py` dengan penonaktifan rate limiter saat pengujian otomatis.
  - Seluruh test suite (11 test items) terverifikasi 100% lulus saat dijalankan dengan `pytest` maupun direct runner (`python tests/test_*.py`).
- **Pembersihan `.gitignore`**:
  - Seluruh baris yang mengabaikan file test dihapus dari `.gitignore` sehingga seluruh test suite terlacak secara utuh di repositori GitHub.
- **Pembaruan GitHub Actions CI Pipeline (`.github/workflows/ci.yaml`)**:
  - Step eksekusi pengujian diperbarui dari `python app/test_master_data.py` menjadi `pytest tests/` untuk menjalankan seluruh rangkaian test integrasi PostgreSQL secara otomatis pada runner GitHub.

### 00. Seller Orders & Custom Orders Integration (`toti-cakery-fe` Support)
- **Seller Orders List & Detail Endpoints (`app/api/routes/order.py`)**:
  - `GET /orders`: Mengembalikan seluruh pesanan toko untuk Seller/Admin dengan relasi eager loading lengkap (Customer, OrderItems, Product, Invoice, Payments, `amount_paid`, `amount_due`). Dilindungi `require_admin_or_owner`.
  - `GET /orders/{order_id}`: Mengembalikan detail pesanan spesifik untuk Seller/Admin.
- **Custom Order Creation (`POST /orders/custom`)**:
  - Endpoint baru untuk admin/seller membuat pesanan kustom tanpa master produk (`product_id = null`).
  - Otomatis melakukan upsert record `Customer` berdasarkan `customer_name` dan `customer_phone` (ter-normalisasi E.164).
  - Mengisi `custom_product_name`, `jumlah` (qty), `subtotal`, dan `hpp_snapshot = 0.00`.
  - **Bypassing Stock Deduction**: Melewati deduksi stok bahan baku / resep karena item tidak terhubung ke master produk.
  - Otomatis membuat `Invoice` dengan nomor `INV-{YYYYMMDD}-{order_id}`, `total_tagihan = total_harga_pesanan`, status `unpaid`.
  - Menetapkan `created_via = "seller"`.
- **Order Status Update & Automatic Stock Restoration (`PATCH /orders/{order_id}/status`)**:
  - Mendukung update status pesanan: `pending`, `in_process`, `ready`, `delivered`, `picked_up`, `cancelled`.
  - **Auto Stock Restoration on Cancellation**: Jika status pesanan diubah ke `cancelled`, sistem secara otomatis mengembalikan stok bahan baku untuk item pesanan yang memiliki resep terkait menggunakan mekanisme **Optimistic Locking** (`version = version + 1`).
  - Memicu webhook chatbot jika status diubah ke `ready`.
- **Database Model & Migration (`app/models/order.py`, `app/core/migrations.py`, `app/main.py`)**:
  - `OrderItem`: Menjadikan kolom `product_id` `nullable=True`, menambahkan kolom `custom_product_name VARCHAR(255)`.
  - `Order`: Menambahkan kolom `notes VARCHAR(1000)`, `due_date TIMESTAMPTZ`, dan `payment_method_preference VARCHAR(50)`.
  - Menambahkan migration idempotent `ensure_order_columns` pada lifespan startup.
- **Pydantic Schemas (`app/schemas/order.py`)**:
  - Menambahkan `CustomerOrderOut`, `CustomOrderItemCreate`, `CustomOrderCreate`.
  - Memperkaya `OrderItemOut` dengan `custom_product_name: Optional[str]` dan `product_id: Optional[int]`.
  - Memperkaya `OrderOut` dengan `customer: Optional[CustomerOrderOut]`, `order_items: list[OrderItemOut]`, `notes`, `due_date`, `payment_method_preference`, `amount_paid`, dan `amount_due`.
- **Automated Tests (`app/test_seller_orders.py`)**:
  - Pengujian terisolasi (SQLite In-Memory) untuk RBAC (`GET /orders`), pembuatan custom order, bypass stok, relasi customer & invoice, kalkulasi pembayaran, order buyer biasa dengan deduksi stok resep, serta pemulihan stok saat status diubah ke `cancelled`.

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

---

### Skenario 6: Testing Service-to-Service Refund & Chatbot Webhook Triggers

1. **Jalankan Automated Test Suite Terisolasi**:
```bash
pytest tests/test_chatbot_refund_webhook.py -v
```
Semua 5 test case akan memverifikasi:
- Penolakan HTTP 403 Forbidden saat chatbot memanggil refund dengan nomor WA yang tidak cocok / tidak diberikan.
- Penolakan HTTP 400 Bad Request saat pesanan sudah berstatus `in_process` (strict state machine guard).
- Keberhasilan refund otomatis pesanan `pending` dengan pengembalian stok, invoice & payment refunded, order cancelled, dan pemicu webhook `/refunded`.
- Fleksibilitas refund manual Admin menggunakan token JWT.
- Pemicu webhook `/paid` saat transaksi Midtrans mencapai status `Success`.

2. **Manual Test via cURL (Service Key Auth)**:
- **Refund Berhasil (Order Pending, Nomor WA Cocok)**:
```bash
curl -X POST "http://localhost:8000/orders/1/refund" \
     -H "X-Service-Key: <CHATBOT_SERVICE_KEY>" \
     -H "Content-Type: application/json" \
     -d '{"reason": "Pelanggan membatalkan pesanan", "nomor_wa": "081234567890"}'
```
*Ekspektasi*: HTTP 200 OK dengan format response:
```json
{
  "message": "Order refund processed successfully",
  "order_id": 1,
  "status": "cancelled",
  "payment_status": "refunded",
  "refund_mode": "auto" // atau "manual" jika metode pembayaran VA/QRIS (HTTP 412)
}
```
dan webhook `POST {CHATBOT_URL}/webhook/internal/orders/1/refunded` tertembak **SETELAH** commit database selesai.

- **Refund Ditolak Karena Nomor WA Berbeda**:
```bash
curl -X POST "http://localhost:8000/orders/1/refund" \
     -H "X-Service-Key: <CHATBOT_SERVICE_KEY>" \
     -H "Content-Type: application/json" \
     -d '{"reason": "Batal", "nomor_wa": "089999999999"}'
```
*Ekspektasi*: HTTP 403 Forbidden `{"detail": "Nomor WhatsApp tidak cocok dengan data pemesan"}`.

---

### Skenario 7: Penyesuaian Logika Query Produk, Schema Ketersediaan Stok & Proteksi Validasi Order

#### A. Ringkasan Perubahan
1. **Schema Layer (`app/schemas/product.py`)**:
   - Menambahkan field eksplisit ketersediaan stok pada `ProductOut`:
     * `is_available: bool` (status ketersediaan manual dari seller).
     * `stock_quantity: int` (ketersediaan fisik porsi kue berdasarkan stok bahan di `stock_items` dan takaran di `recipes`).
     * `is_in_stock: bool` (computed property: `is_available == True` dan `stock_quantity > 0`).
   - Menyediakan alias Pydantic schema: `ProductResponse = ProductOut` dan `ProductRead = ProductOut`.
   - Menambahkan field `is_available` pada `ProductCreate` (default `True`) dan `ProductUpdate` (opsional `bool`).

2. **Model & Database Migration Layer (`app/models/product.py`, `app/core/migrations.py`)**:
   - Mengubah `is_available` pada model SQLAlchemy `Product` menjadi kolom database:
     `is_available = Column(Boolean, default=True, nullable=False)`
   - Menambahkan computed property `@property def stock_quantity(self) -> int` yang menghitung takaran terendah dari seluruh bahan resep produk (`int(stok_tersedia // jumlah_dibutuhkan)`).
   - Menambahkan computed property `@property def is_in_stock(self) -> bool` yang mengevaluasi `bool(self.is_available and self.stock_quantity > 0)`.
   - Memperbarui migration otomatis `ensure_product_columns` untuk menambahkan kolom `is_available` pada PostgreSQL.

3. **Service & Route Layer (`app/services/product_service.py`, `app/api/routes/product.py`)**:
   - Menambahkan query parameter `only_available: bool = Query(False)` pada endpoint `GET /products/`.
   - Default query tanpa param `only_available` tetap mengembalikan seluruh katalog produk aktif (termasuk yang stok habis / 0) dengan field `stock_quantity = 0` dan `is_in_stock = False`.
   - Query `GET /products/?only_available=true` menyaring hanya produk yang `is_in_stock == True`.
   - Endpoint `GET /products/{id}` mengembalikan detail produk beserta informasi ketersediaan stok.

4. **Order Checkout Protection (`app/services/order_service.py`)**:
   - Menambahkan validasi ketersediaan stok ketat pada `create_new_order`:
     * Jika `product.is_available == False`, melempar HTTP 400: `"Produk '{nama_produk}' sedang tidak tersedia."`
     * Jika `product.is_in_stock == False` atau `product.stock_quantity <= 0`, melempar HTTP 400: `"Stok produk '{nama_produk}' sedang habis."`
     * Jika kuantitas pesanan melebihi stok yang ada, melempar HTTP 400: `"Jumlah pesanan ({jumlah}) melebihi stok yang tersedia ({stock_quantity}) untuk produk '{nama_produk}'."`

#### B. Pengujian Terotomasi
Jalankan test suite terisolasi baru:
```bash
venv/bin/pytest tests/test_product_stock_catalog.py -v
```
Seluruh 4 test cases memvalidasi:
- Kesesuaian schema Pydantic `ProductResponse` / `ProductRead` dan kalkulasi computed `is_in_stock`.
- Kalkulasi `stock_quantity` dari relasi `recipes` & `stock_items`.
- Perilaku default katalog `GET /products/` menampilkan produk habis, dan pemfilteran saat `only_available=true`.
- Penolakan pesanan saat produk habis atau melebihi stok dengan HTTP 400 dan pesan error yang tepat.

---

### Skenario 8: Perbaikan Akses Financial Report (`GET /reports/financial`), Null-Safety Query & Pydantic Schema Hardening

#### A. Ringkasan Perubahan
1. **Authorization & Error Shielding Route Layer (`app/api/routes/report.py`)**:
   - Menyelaraskan izin dependensi `get_financial_report` dan `get_analytics_report` dari `require_owner` menjadi `require_internal_user` (mengizinkan peran Owner, Admin, dan Staff pada Seller Dashboard untuk mengakses laporan keuangan).
   - Menambahkan blok penanganan eksepsi `try-except` dan logging terstruktur pada endpoint `GET /reports/financial`, `GET /reports/analytics`, dan `GET /reports/summary` untuk mencegah HTTP 500 unhandled exceptions.

2. **Query & Repository Hardening (`app/repositories/report_repo.py`)**:
   - Memasang `func.coalesce` pada kalkulasi `Invoice.total_tagihan`, `OrderItem.hpp_snapshot`, dan `OrderItem.subtotal` untuk memastikan tidak terjadi operasi aritmatika bernilai `NULL` pada baris legacy order di PostgreSQL.

3. **Schema Layer Null-Safety (`app/schemas/product.py`, `app/schemas/stock.py`)**:
   - Memperbarui fungsi pembantu `_round2(v, default=None)` untuk menerima nilai default saat `v` bernilai `None`.
   - Mengamankan validator `StockOut` (`round_money`, `round_stock`, `round_alert`) terhadap `None` input.
   - Menjadikan `ProductOut.hpp_total` opsional dengan default `Decimal("0.00")` dan validator `round_hpp`.

#### B. Pengujian Terotomasi
- Menambahkan test case `test_financial_report_endpoint_authorization_and_query` pada `tests/test_financial_report.py`.
- Seluruh 54 test cases lulus 100%:
```bash
venv/bin/pytest tests/ -v
# 54 passed, 1 warning in 10.66s
```

---

### Skenario 9: Implementasi Opsi B — Serialization Layer Hardening untuk Legacy Orders Null Safety (`GET /orders/buyer` & `GET /orders`)

#### A. Ringkasan Perubahan
1. **Pydantic Before-Validators pada Schema Pesanan (`app/schemas/order.py`)**:
   - `OrderOut`:
     - Menambahkan `@field_validator("created_via", mode="before")` yang meng-intercept nilai `None`/kosong pada legacy order di database dan memberikan fallback historis `"chatbot"`.
     - Menambahkan `@field_validator("metode_pengiriman", mode="before")` dengan fallback `"pickup"`.
     - Menambahkan `@field_validator("status", mode="before")` dengan fallback `"pending"`.
     - Mempertahankan validator `total_harga_pesanan` dengan fallback `Decimal("0.00")`.
   - `OrderItemOut`:
     - Menambahkan `@field_validator("jumlah", mode="before")` dengan fallback `1`.
     - Memperkuat `@field_validator("custom_decoration_charge", "subtotal", "hpp_snapshot", mode="before")` agar mengonversi `None` menjadi `Decimal("0.00")` sebelum validasi tipe Pydantic.
   - `CustomerOrderOut`:
     - Menambahkan before-validators untuk `nama` (fallback `"Customer"`) dan `nomor_wa` (fallback `""`).
   - `InvoiceOut`:
     - Menambahkan before-validators untuk `status` (fallback `"unpaid"`) dan `total_tagihan` (fallback `Decimal("0.00")`).

2. **Keunggulan Opsi B**:
   - **Zero Risk di Database Production**: Tidak memerlukan migrasi `SET NOT NULL` yang berpotensi mengunci tabel live atau memicu kegagalan saat ada payload insert legacy.
   - **Integritas Data Historis Terjaga**: Data historis di database tetap apa adanya, sedangkan layer respons API selalu menjamin payload JSON yang rapi, lengkap, dan tanpa field `null` yang merusak Frontend.
   - **Frontend Tetap Tanpa Perubahan**: Frontend (Buyer Web & Seller Dashboard) menerima data dalam format schema yang 100% konsisten.

#### B. Pengujian Terotomasi
- Menambahkan verifikasi `created_via=None`, `custom_decoration_charge=None`, dan `hpp_snapshot=None` pada suite test `tests/test_buyer_orders_payments.py`.
- Seluruh 54 test cases lulus 100%:
```bash
venv/bin/pytest tests/ -v
# 54 passed, 1 warning in 10.69s (100% PASSED)
```

---

### Skenario 10: Perbaikan Crash HTTP 500 pada `GET /orders` & `GET /reports/financial`

#### A. Ringkasan Perubahan
1. **Fix Serialization Error pada Order Schemas (`app/schemas/order.py`)**:
   - `OrderItemRead` / `OrderItemOut`:
     - Menambahkan `@field_validator("custom_decoration_charge", "hpp_snapshot", mode="before")` yang mengembalikan `Decimal("0.00")` (atau `0.0`) jika nilainya `None`.
     - Menambahkan `@field_validator("subtotal", mode="before")` dengan fallback aman `Decimal("0.00")`.
     - Menambahkan `@field_validator("jumlah", mode="before")` dengan fallback `1`.
   - `OrderRead` / `OrderOut`:
     - Menambahkan `@field_validator("created_via", mode="before")` yang mengembalikan `"BUYER_SITE"` jika nilainya `None` atau tidak ada.
     - Menyediakan alias DTO eksplisit: `OrderRead = OrderOut`, `OrderResponse = OrderOut`, `OrderItemRead = OrderItemOut`, `OrderItemResponse = OrderItemOut`.

2. **Fix Null Safety pada Financial Report Calculation (`app/repositories/report_repo.py`)**:
   - Memastikan seluruh kalkulasi SQL menggunakan `func.coalesce(OrderItem.hpp_snapshot, Decimal("0.00"))` dan `func.coalesce(OrderItem.subtotal, Decimal("0.00"))` agar baris legacy yang bernilai `NULL` tidak memicu `TypeError` di database maupun di Python backend.

#### B. Pengujian Terotomasi
- Menjalankan unit test khusus:
```bash
venv/bin/pytest tests/test_seller_orders.py tests/test_financial_report.py -v
# 6 passed in 1.12s
```
- Menjalankan seluruh test suite backend:
```bash
venv/bin/pytest tests/ -v
# 54 passed, 1 warning in 10.54s (100% PASSED)
```


