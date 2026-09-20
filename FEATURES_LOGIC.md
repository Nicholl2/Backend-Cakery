# Business Logic & Automations

Dokumentasi lengkap logika bisnis, aturan validasi, dan otomasi alur kerja Backend Toti Cakery.

---

## 1. Pembuatan Order, Reservasi Stok & Optimistic Concurrency Control

Alur kerja saat endpoint `POST /orders` dipanggil oleh Chatbot atau Client:
1. **Validasi Tagihan Aktif**:
   Sistem memeriksa apakah customer memiliki order yang belum lunas (`InvoiceStatusEnum.unpaid` atau `partial`). Jika ada, pembuatan order baru ditolak dengan HTTP `409 Conflict`.
2. **Validasi Produk, Ketersediaan Stok & Minimum Order**:
   - Produk harus berstatus `is_active = True` dan sudah memiliki `harga_jual`.
   - **Ketersediaan Manual Seller**: Produk wajib memiliki status `is_available = True`. Jika seller menonaktifkan ketersediaan manual produk, sistem melempar HTTP `400 Bad Request` ("Produk 'X' sedang tidak tersedia.").
   - **Ketersediaan Stok Bahan Baku (`is_in_stock`)**: Produk wajib memiliki `is_in_stock == True` dan `stock_quantity > 0`. Jika stok habis, sistem melempar HTTP `400 Bad Request` ("Stok produk 'X' sedang habis.").
   - **Batas Kuantitas Pesanan**: Kuantitas pesanan (`jumlah`) tidak boleh melebihi `stock_quantity` yang tersedia, dan tidak boleh kurang dari `minimum_order` produk.
3. **Kalkulasi Kebutuhan Bahan Baku (Bill of Materials)**:
   - Untuk setiap produk dalam pesanan, sistem mengalikan kuantitas pesanan dengan takaran bahan baku di tabel `recipes`:
     $$\text{total\_needed} = \text{jumlah pesanan} \times \text{jumlah\_dibutuhkan}$$
   - Sistem menjumlahkan kebutuhan per `stock_item_id` dan memvalidasi apakah `stok_tersedia >= total_needed`.
4. **Pengurangan Stok dengan Optimistic Locking & Auto-Retry**:
   - Pengurangan stok dieksekusi dengan query berfilter versi:
     ```sql
     UPDATE stock_items 
     SET stok_tersedia = stok_tersedia - :total_needed, version = version + 1
     WHERE id = :stock_id AND version = :current_version;
     ```
   - **Auto-Retry Loop (Maksimal 3 Kali)**: Jika `rowcount == 0` (terjadi modifikasi bersamaan oleh transaksi checkout lain), sistem tidak langsung menolak pesanan melainkan me-refetch data bahan baku terkini dan mencoba kembali hingga 3 percobaan (`MAX_STOCK_RETRY = 3`).
   - Jika setelah 3 kali percobaan tetap terjadi conflict atau sisa stok tidak mencukupi kebutuhan pesanan, transaksi di-rollback dan melempar HTTP `400 Bad Request` untuk mencegah overselling.
5. **Snapshot HPP & Pembuatan Invoice**:
   - Nilai HPP produk saat transaksi disimpan ke kolom `order_items.hpp_snapshot` untuk integritas audit laba kotor di masa mendatang.
   - Nomor invoice dibuat dengan format: `INV-YYYYMMDD-{order_id}` dengan status awal `unpaid`.

---

## 2. Pembatalan Order & Pemulihan Stok Bahan Baku

Alur kerja saat endpoint `POST /orders/{order_id}/cancel` dipanggil:
1. **Pessimistic Row Lock & Proteksi Status Invoice**:
   - Query mengambil pesanan menggunakan `with_for_update()` untuk mencegah race condition double-cancellation oleh request simultan.
   - Hanya pesanan yang status invoice-nya masih murni `unpaid` yang dapat dibatalkan otomatis. Pesanan yang sudah dibayar penuh (`paid`) atau memiliki DP (`partial`) ditolak dengan HTTP `409 Conflict`.
2. **Restorasi Stok Bahan Baku**:
   - Sistem menghitung kembali kuantitas bahan baku dari resep produk yang ada di `order_items`.
   - Mengembalikan kuantitas ke `stock_items.stok_tersedia` menggunakan Optimistic Locking (`version = version + 1`).
3. **Pembaruan Status & State Machine**:
   Status order diubah menjadi `cancelled` setelah lolos validasi `is_valid_order_transition`.

---

## 3. Integrasi Pembayaran Midtrans Core API (Headless Charge)

Alur kerja pada endpoint `POST /payments`:
1. **Validasi Anti-Tampering Nominal**:
   - Pembayaran tipe `full` wajib sama persis dengan `order.total_harga_pesanan`.
   - Pembayaran tipe `dp` wajib bernilai $50\%$ dari `order.total_harga_pesanan` ($0.5 \times \text{total}$).
   - Jika nominal tidak cocok dengan kalkulasi backend, sistem menolak dengan HTTP `400 Bad Request`.
2. **Dispatch Transaksi ke Midtrans**:
   - Mengirim request HTTP POST ke `/charge` Midtrans Sandbox/Production menggunakan `Basic Auth` (`midtrans_server_key`).
   - Mendukung metode `bank_transfer` (BCA Virtual Account) dan `qris` (dynamic QR code URL).
3. **Pencatatan Record Pembayaran & Audit Trail**:
   - Membuat record baru di tabel `payments` dengan status awal `Pending` dan tipe `DP` atau `Final`.
   - Menghasilkan structured log `[PAYMENT_AUDIT] charge_created`.

---

## 4. Otomasi Webhook Settlement, Idempotency & State Machine

Alur kerja saat Midtrans memanggil webhook listener `POST /payments/notify`:
1. **Verifikasi Integritas Request (SHA512 Signature Key)**:
   Backend menghitung hash SHA-512 dari kombinasi:
   $$\text{hash} = \text{SHA512}(\text{order\_id} + \text{status\_code} + \text{gross\_amount} + \text{midtrans\_server\_key})$$
   Jika signature tidak cocok, request ditolak dengan HTTP `400 Bad Request (Invalid Signature)`.
2. **Row-Level Lock (`SELECT ... FOR UPDATE`)**:
   Query payment mengeksekusi `with_for_update()` pada PostgreSQL untuk memblokir callback duplikat yang datang bersamaan secara paralel.
3. **Idempotency Guard**:
   Jika transaksi sudah berstatus terminal (`Success`, `Failed`, atau `Refunded`), pemrosesan webhook duplikat langsung diabaikan (`skipped`) secara aman tanpa mutasi ganda ke Invoice atau Order.
4. **State Machine Enforcement**:
   Validasi satu arah via `is_valid_payment_transition` memastikan status `Success` tidak pernah dapat di-rollback kembali ke `Pending` atau `Failed` oleh webhook stale yang datang terlambat.
5. **Pemetaan Status Transaksi**:
   - `settlement` / `capture` $\rightarrow$ `PaymentStatusEnum.success`
   - `deny` / `cancel` / `expire` $\rightarrow$ `PaymentStatusEnum.failed`
   - `pending` $\rightarrow$ `PaymentStatusEnum.pending`
6. **Kalkulasi Akumulasi & Transisi Status Invoice/Order**:
   Jika status payment berubah menjadi `Success`:
   - Sistem menghitung total akumulasi pembayaran sukses:
     $$\text{total\_success} = \sum (\text{Payment.jumlah\_bayar}) \quad \text{dimana status} = \text{'Success'}$$
   - **Kondisi Lunas Penuh**:
     Jika $\text{total\_success} \ge \text{invoice.total\_tagihan}$:
     - `invoice.status` diubah menjadi `paid`.
     - `order.status` tetap berada di `pending` (memungkinkan pembeli mengajukan refund sebelum admin secara manual memulai proses produksi kue di dapur).
   - **Kondisi Uang Muka (DP)**:
     Jika $\text{total\_success} < \text{invoice.total\_tagihan}$:
     - `invoice.status` diubah menjadi `partial`.
7. **Structured Audit Logging**:
   Seluruh transisi status dan mutasi otomatis dicatat dengan prefix `[PAYMENT_AUDIT]` yang mencakup `transaction_id`, status lama, status baru, dan nominal transaksi.

---

## 5. Notifikasi Webhook Kesiapan Pesanan (Order Ready Push)

Alur kerja saat Admin/Owner memperbarui status order menjadi `ready` via `PATCH /orders/{order_id}/status`:
1. Status order diperbarui menjadi `ready`.
2. Backend secara otomatis menembak webhook internal ke Chatbot Service:
   - Endpoint: `${settings.chatbot_url}/webhook/internal/orders/{order_id}/ready`
   - Header: `X-Internal-Key: ${settings.chatbot_internal_key}`
3. Chatbot menerima push event ini dan langsung mengirim notifikasi WhatsApp kepada pelanggan bahwa kue/pesanan telah siap diambil atau dikirim.

---

## 6. Autentikasi & Verifikasi WhatsApp Deep Link (Passwordless / OTP) & Mock Mode

Alur verifikasi nomor WhatsApp untuk Buyer Site:
1. **Mulai Sesi (`POST /auth/verify/wa/start`)**:
   - **Real Mode (`WA_VERIFICATION_MODE=real`)**:
     - Mengenerate `nonce` unik 6 karakter alfanumerik (masa berlaku 10 menit).
     - Menghasilkan URL WhatsApp Deep Link: `https://wa.me/<nomor_bot>?text=VERIFIKASI%20<nonce>`.
   - **Mock Mode (`WA_VERIFICATION_MODE=mock` & `ENVIRONMENT != production`)**:
     - Bypass pembuatan deep link WhatsApp asli dan tidak memerlukan konfigurasi chatbot WA number.
     - Otomatis mencatat OTP ke database dengan status `is_verified = True` dan langsung men-generate `verify_token` (UUID).
     - Mengembalikan response DTO berisi `verify_token` dan flag `mock_mode: true` untuk mempermudah testing frontend.
2. **Polling Frontend (`GET /auth/verify/wa/status?nonce=...`)**:
   - Frontend melakukan polling status verifikasi `nonce`.
   - Pada mode Mock atau ketika sudah terverifikasi, backend langsung mengembalikan status `"verified"` beserta `verify_token`.
3. **Konfirmasi Chatbot di Real Mode (`POST /auth/verify/wa/confirm`)**:
   - Saat customer mengklik link dan mengirim pesan WhatsApp, chatbot memvalidasi nomor pengirim (`sender_phone`).
   - Normalisasi nomor telepon ke format E.164 internasional (7–15 digit; strip simbol/spasi/plus, `0...` $\rightarrow$ `62...`, `620...` $\rightarrow$ `62...`, kode negara internasional seperti `1...`, `60...`, `65...`, `44...` dipertahankan).
   - Jika nomor pengirim tidak cocok dengan target pendaftaran, `attempt_count` bertambah (maksimal 3 kali percobaan sebelum diblokir HTTP `429 Too Many Requests`).
   - Jika cocok, `otp.is_verified` diubah ke `True` dan backend men-generate `verify_token` (UUID).
4. **Konsumsi Token Single-Use**:
   - Token `verify_token` digunakan sekali pakai untuk registrasi akun baru (`/auth/buyer/register`) atau login instan (`/auth/buyer/login/otp`).
5. **Guardrail Keamanan Produksi**:
   - Jika `ENVIRONMENT="production"`, pengaturan `WA_VERIFICATION_MODE` secara otomatis dipaksa menjadi `"real"` oleh Pydantic validator untuk mencegah pembobolan verifikasi di server produksi.

---

## 7. Mekanisme Live Chat Human Takeover

Alur kerja pengalihan percakapan dari AI Chatbot ke Admin/Owner:
1. **Pemeriksaan Status Takeover (`GET /customers/{nomor_wa}/takeover`)**:
   Sebelum membalas pesan pengguna di WhatsApp, chatbot wajib memanggil endpoint ini.
2. **Kondisi Bypass Bot**:
   Jika `human_takeover_active = True` DAN `takeover_expires_at` belum lewat waktu (`is_expired = False`), chatbot dilarang merespons pesan agar admin dapat bercakap-cakap langsung dengan customer.
3. **Aktivasi Takeover (`POST /customers/{nomor_wa}/takeover`)**:
   Admin mengaktifkan takeover dengan menyertakan batas waktu kedaluwarsa (`expires_at`).
4. **Daftar Handler Admin (`GET /admin/takeover-handlers`)**:
   Mengambil nomor WhatsApp seluruh user internal yang memiliki flag `handles_takeover = True` untuk menerima notifikasi eskalasi live-chat.

---

## 8. Manajemen Bill of Materials (BOM) & Sinkronisasi HPP

1. **Formula Perhitungan HPP**:
   $$\text{HPP Total Produk} = \sum_{i=1}^{n} (\text{jumlah\_dibutuhkan}_i \times \text{harga\_per\_satuan}_i)$$
2. **Otomasi Sinkronisasi Instan**:
   - Setiap kali bahan baku ditambahkan (`POST /recipes/...`), takarannya diubah (`PUT /recipes/...`), atau bahan dihapus (`DELETE /recipes/...`), backend langsung menghitung ulang total HPP dan memperbarui kolom `products.hpp_total`.
3. **Cascading Recalculation via Purchase Order**:
   - Saat Purchase Order baru dibuat (`POST /purchases/purchases`), backend mendeteksi seluruh bahan yang terpengaruh dan secara otomatis menghitung ulang HPP pada seluruh produk yang menggunakan bahan baku tersebut.

---

## 9. Kebijakan Penetapan Harga (Pricing Policy & Warning)

1. **Kewenangan Owner**:
   Hanya akun Owner (`require_admin_or_owner` / `require_owner`) yang berhak menetapkan dan mengubah `harga_jual` produk.
2. **Non-Blocking Margin Warning**:
   - Jika harga jual yang diinput lebih rendah dari total HPP produk ($\text{harga\_jual} < \text{hpp\_total}$), sistem mengembalikan flag `warning_below_hpp = True`.
   - Sistem tidak memblokir penetapan harga tersebut, namun memberi peringatan transparan kepada Owner.
3. **Audit Riwayat Harga**:
   Setiap perubahan harga otomatis dicatat ke tabel `price_histories` lengkap dengan `harga_jual_lama`, `harga_jual_baru`, `hpp_saat_itu`, dan `changed_by`.

---

## 10. Agregasi Finansial & Analitik Laba/Rugi (P&L)

1. **Pendapatan (Revenue)**: Dihitung dari $\sum(\text{Payment.jumlah\_bayar})$ pada transaksi berstatus `Success`.
2. **Beban Pokok Penjualan (COGS / HPP Penjualan)**: Dihitung dari $\sum(\text{OrderItem.hpp\_snapshot} \times \text{OrderItem.jumlah})$ pada pesanan yang valid (non-cancelled).
3. **Laba Kotor (Gross Profit)**: $\text{Revenue} - \text{COGS}$.
4. **Biaya Operasional (Operating Expenses)**: $\sum(\text{Expense.jumlah})$ berdasarkan kategori (gaji, listrik, sewa, dll.).
5. **Laba Bersih (Net Profit)**: $\text{Gross Profit} - \text{Operating Expenses}$.
6. **Top Selling Products**: Ranking produk berdasarkan total kuantitas terjual dan kontribusi omzet pada rentang periode yang dipilih.

---

## 11. Rate Limiting & Anti-Abuse Protection

Sistem menerapkan pembatasan frekuensi pemanggilan endpoint (Rate Limiting) berbasis memori (`slowapi`):
1. **Pencegahan Spam Order (`POST /orders`, `POST /orders/buyer`, `POST /orders/custom`)**:
   - Dibatasi maksimal **5 request per menit per alamat IP**.
   - Mencegah serangan pengurasan stok bahan baku dan pembuatan invoice fiktif.
2. **Pencegahan Brute-Force Kredensial (`POST /auth/login`, `POST /auth/buyer/register`, `POST /auth/buyer/login`)**:
   - Dibatasi maksimal **10 request per menit per alamat IP**.
3. **Pencegahan Spam Kode Verifikasi WhatsApp (`POST /auth/verify/wa/start`)**:
   - Dibatasi maksimal **6 request per menit per alamat IP** untuk melindungi gateway WhatsApp dan nomor bot.
5. **Penanganan Pelanggaran**:
   - Request yang melebihi batas kuota akan langsung ditolak oleh middleware dengan respon standar HTTP `429 Too Many Requests`.

---

## 12. Validasi Input & Pencegahan Serangan XSS / Script Injection

Sistem menerapkan proteksi ketat (Schema Hardening) berbasis Pydantic Validator untuk seluruh endpoint (Auth, Customers, Orders, Reviews, dll):
1. **Sanitasi Teks Terpusat (Global Text Sanitizer)**:
   - Modul `app/utils/sanitize.py` dipanggil via `@field_validator` untuk memproses dan menolak input yang mengandung tag HTML, XML, maupun script berbahaya (contoh: `<script>`, `javascript:`, `<iframe>`, `<object>`, `<embed>`, dll).
   - Menolak *Cross-Site Scripting (XSS)* langsung di level API. Input akan ditolak dengan response `400/422` jika terdeteksi malicious.
2. **Aturan Karakter & Panjang (Length Bounds)**:
   - **Username**: Dibatasi maksimal 25 karakter. Hanya diizinkan menggunakan alfanumerik, underscore, dan hyphen (Regex: `^[a-zA-Z0-9_-]+$`).
   - **Nomor Telepon**: Dibatasi panjang antara 10 hingga 16 karakter, hanya angka, dan dinormalisasi menjadi format standar E.164 (`628...`).
   - **Email**: Maksimal 100 karakter dengan validasi format standar.
   - **Teks Pendek (Nama)**: Maksimal 100 karakter.
   - **Teks Bebas (Alamat, Catatan/Notes)**: Dibatasi maksimal 500 karakter.
   - **Teks Ulasan (Review)**: Dibatasi maksimal 1000 karakter.

---

## 13. Logika Katalog Produk, Ketersediaan Stok & Resep (BOM)

Sistem backend mengintegrasikan pemisahan status operasional seller dan ketersediaan fisik bahan baku secara akurat:
1. **Dua Layer Status Ketersediaan**:
   - `is_available: bool`: Status manual dari seller (Staff/Admin/Owner) yang tersimpan di kolom database `products.is_available` (default `True`). Seller dapat menonaktifkan produk kapan saja (misal: menu kue sedang tidak diproduksi hari ini).
   - `stock_quantity: int`: Jumlah porsi kue yang dapat diproduksi secara fisik berdasarkan ketersediaan bahan baku di tabel `stock_items` dan takaran per kue di tabel `recipes`:
     $$\text{stock\_quantity} = \min_{r \in \text{recipes}} \left\lfloor \frac{\text{stock\_item.stok\_tersedia}}{\text{r.jumlah\_dibutuhkan}} \right\rfloor$$
     Jika produk belum memiliki resep atau salah satu bahan baku habis, bernilai `0`.
   - `is_in_stock: bool`: Status ketersediaan komputasi gabungan:
     $$\text{is\_in\_stock} = \text{is\_available} \land (\text{stock\_quantity} > 0)$$
2. **Perilaku Endpoint Katalog (`GET /products`)**:
   - **Default Behavior**: Menampilkan seluruh katalog produk aktif tanpa menyembunyikan atau memfilter keluar produk yang stoknya 0 / habis. Produk dengan stok 0 tetap dikembalikan dengan nilai `stock_quantity = 0` dan `is_in_stock = false` agar calon pembeli tetap dapat melihat katalog lengkap toko.
   - **Filter Query `only_available=true`**: Jika klien mengirimkan query parameter `GET /products?only_available=true`, sistem akan menyaring dan hanya mereturn produk dengan `is_in_stock == True`.
3. **Proteksi Checkout**:
   - Endpoint pemesanan (`POST /orders` dan `POST /orders/buyer`) memvalidasi status ketersediaan secara ketat sebelum reservasi stok.
   - Jika pembeli mencoba memesan produk dengan `is_in_stock == False` atau `stock_quantity == 0`, pesanan ditolak dengan HTTP `400 Bad Request` ("Stok produk 'X' sedang habis.").
   - Jika kuantitas yang dipesan melebihi `stock_quantity`, pesanan ditolak dengan HTTP `400 Bad Request`.

---

## 14. Akuntansi Laporan Keuangan, Cash Basis Flow & Konsistensi Date Basis

Untuk memastikan laporan laba rugi (`GET /reports/financial`) akurat dan mencerminkan prinsip akuntansi (*matching principle* serta arus kas *cash basis*):
1. **Konsistensi Basis Tanggal (Settlement-Based Allocation)**:
   - Revenue dan HPP (Harga Pokok Penjualan) dihitung menggunakan basis tanggal penyelesaian pembayaran yang sama (`settled_at` atau `created_at` pembayaran sukses).
   - Jika pesanan dibuat di akhir suatu bulan (misalnya 30 Januari) dan baru dibayar lunas pada bulan berikutnya (misalnya 2 Februari), maka Revenue dan HPP pesanan tersebut sama-sama dialokasikan ke bulan Februari. Hal ini mencegah bergesernya *Gross Profit* dan *Net Profit* antarperiode.
2. **Eksklusi Total Pesanan Unpaid / Pending**:
   - Query HPP dan Revenue hanya memperhitungkan pesanan yang status invoice-nya sudah lunas (`InvoiceStatusEnum.paid`) dan memiliki pembayaran berstatus `Success`.
   - Pesanan berstatus *unpaid* atau *pending payment* **dikeluarkan total** dari perhitungan Revenue, HPP, Gross Profit, dan Net Profit.
3. **Arus Kas Masuk Nyata (`cash_received` - Cash Basis Flow & Historical Integrity)**:
   - Menghitung seluruh akumulasi dana riil yang masuk dari transaksi pembayaran yang diproses sukses (`Payment.payment_status.in_(['Success', 'Refunded'])`) berdasarkan `Payment.created_at` pada rentang periode filter (`start_date` s.d. `end_date`).
   - **Integritas Historis (Historical Integrity)**: Transaksi pembayaran yang berhasil diproses di masa lalu tidak dihapus dari `cash_received` periode tersebut meskipun di kemudian hari dibatalkan dan direfund.
4. **Arus Kas Keluar via Refund (`cash_refunded`)**:
   - Menghitung total pengembalian dana kepada pelanggan (`Payment.payment_status == 'Refunded'`) berdasarkan tanggal eksekusi refund (`coalesce(Payment.updated_at, Payment.created_at)`) yang jatuh dalam rentang periode filter.
5. **Arus Kas Bersih (`net_cash_flow`)**:
   - Mengukur pergerakan kas netto pada periode yang bersangkutan:
     $$\text{net\_cash\_flow} = \text{cash\_received} - \text{cash\_refunded}$$
6. **Perhitungan Piutang Kumulatif (`outstanding_payments`)**:
   - Menghitung SELURUH sisa tagihan invoice yang belum lunas per titik akhir periode (`end_date`), termasuk akumulasi piutang dari pesanan-pesanan pada periode sebelumnya yang belum selesai pembayarannya (`Order.created_at <= end_date`).
   - Formula: $\sum (\text{Invoice.total\_tagihan} - \text{pembayaran\_sukses\_hingga\_end\_date})$ untuk semua invoice berstatus `unpaid` atau `partial` pada order non-cancelled/non-refunded.
7. **Penanganan DP Hangus (`non_refundable_dp_income` / `other_income`)**:
   - Pesanan berstatus `cancelled` yang memiliki transaksi pembayaran sukses (DP) yang tidak direfund diperlakukan sebagai pendapatan lain-lain (*other income*).
   - Nominal DP tersebut tetap tercatat di `cash_received` dan dihitung ke dalam laba bersih:
     $$\text{net\_profit} = \text{gross\_profit} - \text{expenses\_total} + \text{non\_refundable\_dp\_income}$$
8. **Metrik Finansial Tambahan**:
   - `full_product_profitability` / `product_profitability`: Menghitung rincian performa per item produk dari pesanan yang lunas (kuantitas terjual, total revenue, total HPP snapshot, gross profit, dan margin persentase).
   - `supplier_spending`: Mengagregasikan total pengeluaran belanja PO dan frekuensi pesanan per supplier pada rentang periode yang dipilih.

---

## 15. Integrasi Purchase Order ke Inventory & Weighted Average Costing

Sistem mengotomasi pencatatan stok dan pembaruan harga pokok bahan baku saat Purchase Order (PO) diterima:
1. **Pemicu Penerimaan PO (`PUT /purchases/purchases/{purchase_id}`)**:
   - Saat status PO ditandai diterima (`is_received = True` dari sebelumnya `False`), sistem otomatis mencatat `tanggal_diterima = now()`.
2. **Penambahan Stok Bahan Baku (`stok_tersedia`)**:
   - Untuk setiap item pembelian (`PurchaseItem`), kuantitas barang yang diterima (`jumlah`) secara otomatis ditambahkan ke `stock_items.stok_tersedia`.
3. **Kalkulasi Weighted Average Costing**:
   - Harga pokok per satuan bahan baku diperbarui menggunakan rumus rata-rata tertimbang:
     $$\text{harga\_baru} = \frac{(\text{stok\_lama} \times \text{harga\_lama}) + (\text{qty\_masuk} \times \text{harga\_satuan\_baru})}{\text{stok\_lama} + \text{qty\_masuk}}$$
   - Nilai baru disimpan ke `stock_items.harga_per_satuan` dengan pembulatan 4 angka desimal, dan `version` diinkremen.
4. **Rekalkulasi Otomatis HPP Produk Resep Terkait**:
   - Setelah harga bahan baku ter-update, sistem otomatis memicu kalkulasi ulang HPP produk (`Product.hpp_total`) dan harga jual (`Product.harga_jual` jika markup percentage aktif) untuk semua produk resep yang menggunakan bahan baku tersebut.
5. **Proteksi & Idempotensi**:
   - Status PO yang sudah berstatus diterima (`is_received = True`) tidak dapat diubah kembali menjadi belum diterima (`is_received = False`) demi mencegah inkonsistensi stok (melempar HTTP `409 Conflict`).
   - Pembaruan field lain (seperti catatan) pada PO yang sudah diterima tidak akan memicu penambahan stok berulang.

---

## 16. Stabilitas, Keamanan, & Optimasi Performa

Modul ini mengimplementasikan lapisan perlindungan dan efisiensi resource pada Backend FastAPI:

1. **Upload Size Limit (`MaxBodySizeMiddleware`)**:
   - Middleware ASGI global di `app/main.py` membaca header `Content-Length`.
   - Menolak request yang melebihi batas maksimal **5 MB** (5 * 1024 * 1024 byte) dengan status `HTTP 413 Payload Too Large`.
   - Melindungi server dari potensi Denial of Service (DoS) melalui pengunggahan file atau payload berukuran raksasa.

2. **Timeout Handling pada External HTTP Clients**:
   - Pemanggilan API eksternal diwajibkan menyertakan parameter `timeout=10.0` detik secara eksplisit:
     - Midtrans Charge (`payment_service.create_midtrans_charge`)
     - Midtrans Status Check (`payment_service.refresh_if_pending`)
     - Midtrans Refund (`payment_service.process_refund`)
     - Cloudinary Upload (`cloudinary_helper.upload_image_to_cloudinary`)
   - Mencegah thread/event loop worker hanging tanpa batas akibat latency atau network partition di pihak gateway pembayaran atau CDN.

3. **In-Memory Caching (Katalog Produk & FAQ)**:
   - Modul `app/core/cache.py` mengimplementasikan singleton `app_cache` berbasis `TTLCache` (in-memory dict dengan default TTL 300 detik / 5 menit).
   - Diterapkan pada endpoint publik yang bersifat read-heavy:
     - `GET /products/`: Dikelompokkan per kombinasi query parameter (`only_active`, `kategori`, `only_available`).
     - `GET /faq`: Dikelompokkan per kombinasi pagination & filter (`skip`, `limit`, `only_active`).
   - **Cache Invalidation Otomatis**:
     - Setiap operasi mutasi produk (`POST`, `PUT`, `DELETE`, `PATCH /price`, `POST /image`) memicu pembersihan cache `products:*`.
     - Setiap operasi mutasi FAQ (`POST`, `PUT`, `DELETE`) memicu pembersihan cache `faqs:*`.

4. **Uptime & Health Check Endpoint (`GET /health`)**:
   - Endpoint publik tanpa autentikasi untuk health check monitoring (Docker HEALTHCHECK, load balancer, ping tools).
   - Memverifikasi konektivitas nyata database via query `SELECT 1`.
   - Mengembalikan `{"status": "ok", "database": "connected"}` (HTTP 200) jika DB sehat, atau `{"status": "error", "database": "disconnected"}` (HTTP 503) jika DB gagal terhubung.

5. **Spending Cap / Transaction Limit Protection**:
   - Validasi nilai pesanan maksimal **Rp 50.000.000** per order pada `order_service.create_new_order` dan `order_service.create_custom_order`.
   - Mencegah kesalahan input atau serangan manipulasi nominal sebelum request diteruskan ke payment gateway Midtrans.
   - Mengembalikan `HTTP 400 Bad Request` jika batas terlampaui.

---

## 17. Pengelolaan Masa Berlaku Token & Pencabutan Sesi (Session Revocation / Logout)

Untuk memastikan keamanan autentikasi pengguna (Owner, Admin, Staff, dan Buyer):
1. **Durasi Masa Berlaku Token (`ACCESS_TOKEN_EXPIRE_MINUTES = 60`)**:
   - Masa aktif JWT Access Token diset secara default ke **60 menit (1 jam)** pada `app/core/config.py` dan `app/core/security.py`.
   - Setiap token yang di-generate via `create_access_token` menyertakan klaim unik `jti` (JWT ID berbasis UUID v4) dan timestamp `exp`.

2. **Pencabutan Sesi (`POST /auth/logout`)**:
   - Endpoint terproteksi yang mewajibkan autentikasi JWT Bearer token (`Authorization: Bearer <token>`).
   - Saat pengguna melakukan logout dari frontend, backend mengekstrak payload token dan mendaftarkannya ke daftar hitam (*blacklist*) di `app/core/cache.py` (`app_cache`).

3. **Mekanisme Blacklisting Berbasis JTI & Signature**:
   - Token disimpan ke in-memory cache dengan kunci:
     - `blacklist:jti:<jti>`
     - `blacklist:sig:<signature>` (bagian signature JWT)
     - `blacklist:token:<token>`
   - Nilai TTL (*Time To Live*) pada cache diset sama persis dengan sisa waktu kedaluwarsa token (`remaining_ttl = exp - now`). Setelah token kedaluwarsa secara alami, entri cache otomatis terhapus tanpa membebani memori server.
   - Setiap pemanggilan fungsi `decode_token` pada seluruh endpoint terproteksi akan memvalidasi apakah token terdapat pada blacklist. Jika ditemukan, sistem langsung melempar `HTTP 401 Unauthorized` dengan pesan `"Token has been revoked"`.

---

## 18. Validasi Kelayakan Ulasan Produk (Order Eligibility & Duplicate Review Protection)

Untuk menjamin keaslian ulasan dan kredibilitas rating produk di etalase toko:

1. **Foreign Key `order_id` & Relasi Pesanan**:
   - Model `Review` menyertakan kolom `order_id` (Foreign Key ke `orders.id`, `nullable=False`, `index=True`).
   - Setiap ulasan produk terikat langsung ke pesanan spesifik yang telah dilakukan oleh pembeli.

2. **Validasi Kelayakan Pesanan (Order Eligibility Check)**:
   - **Kepemilikan Pesanan**: Sistem memverifikasi bahwa pesanan (`data.order_id`) benar-benar milik akun buyer yang sedang login (`order.customer_id == customer.id`).
   - **Status Penyelesaian**: Ulasan hanya dapat diberikan jika pesanan telah selesai (`OrderStatusEnum.completed`, `OrderStatusEnum.delivered`, atau `OrderStatusEnum.picked_up`). Jika pesanan masih berstatus *pending*, *in_process*, *ready*, atau *cancelled*, sistem menolak ulasan dengan `HTTP 400 Bad Request` ("Hanya pesanan yang sudah selesai yang dapat diulas.").

3. **Validasi Item Produk dalam Pesanan (Product in Order Check)**:
   - Sistem memverifikasi bahwa `product_id` yang ingin diulas benar-benar terdapat dalam daftar item pesanan terkait (`order.order_items`).
   - Jika produk tidak ada dalam pesanan tersebut, request ditolak dengan `HTTP 400 Bad Request` ("Produk tidak terdapat dalam pesanan ini.").

4. **Proteksi Ulasan Ganda (Duplicate Review Protection)**:
   - **Application-Level Check**: Pada `review_service.create_review`, sistem memeriksa apakah kombinasi `(order_id, product_id)` sudah pernah diulas di database. Jika sudah ada, sistem menolak dengan `HTTP 400 Bad Request` ("Anda sudah memberikan ulasan untuk produk pada pesanan ini.").
   - **Database-Level Constraint**: Tabel `reviews` dilengkapi composite Unique Constraint `uq_review_order_product_customer` untuk tuple `(order_id, product_id, customer_id)` dan migrasi otomatis pada PostgreSQL (`CREATE UNIQUE INDEX IF NOT EXISTS uq_review_order_product_customer`).

5. **Rekalkulasi Otomatis Rating & Review Count Produk**:
   - Setiap kali ulasan baru dibuat, diperbarui, atau dihapus, fungsi `recalculate_product_rating` otomatis menghitung ulang `Product.rating` (rata-rata rating) dan `Product.review_count` (jumlah total ulasan).

---

## 19. Keamanan & Stabilitas Alur Pemesanan Buyer (`POST /orders/buyer`)

Untuk mengamankan pengalaman berbelanja pelanggan pada web storefront:

1. **Penanganan Duplicate Customer / Nomor Telepon (`get_or_create`)**:
   - Sistem menggunakan helper `get_phone_variants` (`app/utils/phone.py`) untuk menghasilkan kandidat representasi nomor HP (seperti format `08xx`, `62xx`, `+62xx`).
   - Pada `get_or_create_customer_for_buyer` dan `customer_repo.upsert`, sistem mencari record `Customer` yang sudah ada menggunakan seluruh varian format tersebut.
   - Jika record sudah ditemukan, data customer langsung digunakan kembali tanpa mencoba melakukan operasi `INSERT` yang dapat memicu `UNIQUE constraint failed: customers.nomor_wa` (HTTP 409 Conflict).
   - Apabila belum ada, barulah customer baru dibuat secara aman dengan penanganan exception race condition.

2. **Jaminan Keunikan Nomor Invoice (`nomor_invoice`) & Order ID Midtrans**:
   - Format nomor invoice diperbarui dengan menambahkan suffix acak 6-karakter hexadesimal:
     $$\text{nomor\_invoice} = \text{INV-YYYYMMDD-}\{\text{order\_id}\}\text{-}\{\text{suffix}\}$$
   - Mencegah bentrok nomor invoice saat buyer melakukan pesanan ulang.
   - Kolom `nomor_invoice` pada tabel `invoices` dialokasikan `VARCHAR(50)` untuk menampung format unik secara fleksibel.
   - Pada payment gateway Midtrans, parameter `order_id_midtrans` menyertakan timestamp milidetik dan random suffix (`{nomor_invoice}-PAY-{timestamp_ms}-{suffix}`) agar upaya pembayaran ulang tidak pernah ditolak oleh Midtrans dengan pesan duplicate order.

3. **Exception Handling Terpusat & Anti-Crash CORS (HTTP 400 vs HTTP 500)**:
   - Seluruh alur `POST /orders/buyer` dibungkus dalam blok `try-except Exception as e`.
   - Kesalahan data, validasi stok, atau kegagalan bisnis dikembalikan sebagai `HTTPException(status_code=400, detail=str(e))` lengkap dengan detail pesan kesalahan yang jelas.
   - Mencegah backend melempar unhandled server crash (HTTP 500) yang berisiko menonaktifkan header CORS dan menyulitkan debugging pada Frontend.

---

## 20. Resiliensi Riwayat Pesanan Buyer (`GET /orders/buyer`) & Isolasi Migrasi Enum PostgreSQL

Untuk menjamin kelancaran pengambilan riwayat pesanan pelanggan dan kompatibilitas runtime PostgreSQL di Vercel:

1. **Isolasi Koneksi Autocommit untuk Migrasi Enum PostgreSQL**:
   - Di PostgreSQL, penambahan nilai baru pada tipe enum (`ALTER TYPE orderstatusenum ADD VALUE ...`) dilarang keras dijalankan di dalam blok transaksi aktif (`ActiveSQLTransactionError`).
   - Eksekusi `ensure_order_status_enum` dipisahkan pada koneksi autocommit terpisah (`engine.connect()`) sebelum blok transaksi `engine.begin()` dibuka.
   - Hal ini mencegah status koneksi masuk ke kondisi *transaction aborted*, sehingga migrasi kolom selanjutnya (`payments.settled_at`, `payments.updated_at`, dan tabel relasi `reviews`) terpasang secara andal di database produksi.

2. **Toleransi & Nilai Default pada Schema Serialisasi Pydantic**:
   - Schema `OrderItemOut` dirancang tahan banting (*resilient*) terhadap data pesanan kustom maupun historis yang memiliki nilai `NULL` pada kolom snapshot/biaya (`hpp_snapshot`, `custom_decoration_charge`).
   - Validator `round_money` dan helper `_round2` otomatis memetakan nilai `None` menjadi representasi desimal `Decimal("0.00")` tanpa memicu `ValidationError` (HTTP 500).

3. **Proteksi & Logging Route Level**:
   - Endpoint `GET /orders/buyer` dan `GET /orders/buyer/{id}` dibekali penanganan error menyeluruh dengan logging terstruktur, menjamin kejelasan informasi dan stabilitas sistem.

---

## 21. Resiliensi Daftar Pesanan Toko Seller (`GET /orders`)

Untuk mengamankan listing pesanan pada Seller Dashboard:

1. **Proteksi Kalkulasi Piutang / Amount Due**:
   - Fungsi internal `_attach_payment_amounts` memvalidasi keberadaan `total_tagihan` pada invoice pesanan secara ketat, mencegah error `decimal.InvalidOperation` jika terdapat record pesanan historis dengan data invoice yang tidak lengkap.

2. **Schema Deserialisasi Kebal Data Null**:
   - Schema `CustomerOrderOut`, `InvoiceOut`, dan `OrderOut` dilengkapi nilai default aman pada field yang berpotensi `NULL` pada basis data toko yang sudah berjalan lama.

3. **Exception Shielding**:
   - Route `list_seller_orders` (`GET /orders`) dan `get_seller_order_detail` (`GET /orders/{order_id}`) dibungkus dalam blok `try-except` dengan structured logging untuk menjamin tidak ada unhandled 500 error mentah yang keluar ke frontend.

---

## 22. Serialization Hardening & Null-Safety Laporan Keuangan

1. **Pydantic Before-Validators pada Order Schemas (`app/schemas/order.py`)**:
   - `OrderItemRead` / `OrderItemOut`: `@field_validator("custom_decoration_charge", "hpp_snapshot", mode="before")` mengembalikan `Decimal("0.00")` (atau `0.0`) jika bernilai `None`.
   - `OrderRead` / `OrderOut`: `@field_validator("created_via", mode="before")` mengembalikan `"BUYER_SITE"` jika bernilai `None` atau kosong.
   - Menyediakan alias DTO lengkap: `OrderRead = OrderOut`, `OrderResponse = OrderOut`, `OrderItemRead = OrderItemOut`, `OrderItemResponse = OrderItemOut`.

2. **Null Safety pada Kalkulasi SQL Laporan Keuangan (`app/repositories/report_repo.py`)**:
   - Query agregasi finansial `get_financial_report_data` menggunakan `func.coalesce(OrderItem.hpp_snapshot, Decimal("0.00"))`, `func.coalesce(OrderItem.custom_decoration_charge, Decimal("0.00"))`, dan `func.coalesce(Invoice.total_tagihan, Decimal("0.00"))` untuk mencegah `TypeError` / `NoneType` arithmetic saat mengolah data legacy di PostgreSQL.

---

## 23. Logika Pembayaran Manual & Konfigurasi Koneksi Database

1. **Alur Pembayaran Manual Kasir / Tunai (`POST /payments/manual`)**:
   - Pencatatan pembayaran manual (CASH, TRANSFER, dll.) oleh kasir/staff menghasilkan record `Payment` dengan status `Success` dan menyimpan catatan kasir pada kolom `notes`.
   - Status `Invoice` diubah menjadi `paid` (atau `partial` jika belum lunas).
   - **Preservasi Status Order**: Status `Order` dipertahankan tetap `pending` (tidak secara otomatis diubah ke `in_process`). Hal ini menjaga konsistensi alur antara pembayaran tunai dan pembayaran online, serta memungkinkan pembeli untuk mengajukan pembatalan/refund via chatbot sebelum pesanan diproses di dapur oleh seller.

2. **Deteksi Otomatis SSL PostgreSQL (`app/core/database.py`)**:
   - Pengaturan koneksi `asyncpg` hanya menyertakan parameter `ssl: True` jika `db_url` host mengandung string `"neon.tech"` atau `"-pooler"`.
   - Untuk koneksi PostgreSQL lokal atau kontainer Docker (`postgres:16-alpine`), `ssl` tidak ditambahkan ke `connect_args`, mencegah kegagalan *handshake* SSL pada container database lokal.

---

## 24. Mutasi Profil Buyer (Ganti Password & Ubah Nomor WhatsApp)

Fitur self-service bagi Buyer yang sudah terautentikasi untuk mengelola kredensial akun mereka melalui dua endpoint pada `buyer_router`:

1. **Ganti Password (`POST /buyers/me/change-password`)**:
   - Buyer wajib menyertakan `current_password` (password lama) untuk verifikasi identitas sebelum mengganti password.
   - Password baru (`new_password`) divalidasi minimal 6 karakter oleh Pydantic schema (`BuyerChangePasswordRequest`).
   - Password baru di-hash menggunakan `hash_password()` (bcrypt) sebelum disimpan ke database. Plaintext **tidak pernah** disimpan.
   - **Tidak memerlukan OTP atau verifikasi WA/Email** — cukup verifikasi password saat ini dari sesi yang terautentikasi.
   - Sesi JWT yang sedang aktif **tidak dibatalkan** setelah ganti password.
   - Response: `HTTP 200 { "message": "Password berhasil diperbarui." }`.

2. **Ubah Nomor WhatsApp (`PATCH /buyers/me/phone`)**:
   - Buyer wajib menyertakan `current_password` untuk konfirmasi kepemilikan akun.
   - Nomor baru dinormalisasi ke format E.164 menggunakan `normalize_phone()` (contoh: `0819...` → `6281900000000`).
   - Validasi keunikan nomor dilakukan via `buyer_repo.get_buyer_by_phone()` — jika sudah digunakan buyer lain, request ditolak `HTTP 400`.
   - **Tidak memerlukan OTP atau verifikasi WA** — cukup verifikasi password untuk konfirmasi pemilik asli.
   - Response: `BuyerProfileResponse` dengan nomor HP terbaru.

3. **Forgot/Reset Password via Email (`POST /auth/buyer/forgot-password` & `POST /auth/buyer/reset-password/email`)**:
   - Jika buyer lupa password dan belum login, mereka dapat request OTP reset password ke email.
   - Sistem akan men-generate 6-digit kode OTP (TTL 10 menit) dan menyimpannya di tabel `otp_codes` dengan hash aman (`code_hash`).
   - OTP dikirim ke email menggunakan utility `app/utils/email_helper.py` (via `aiosmtplib`). Jika SMTP belum diatur, OTP hanya di-log ke console (mock mode).
   - Pada endpoint request, API *selalu* mereturn `200 OK` (meskipun email tidak terdaftar) untuk **mencegah email enumeration**.
   - Untuk reset, buyer harus memasukkan `email`, `otp`, dan `new_password`. API memverifikasi kode OTP tersebut dan masa aktifnya, sebelum meng-update password.
   - Token OTP yang sudah dipakai di-mark sebagai `is_used = True` untuk menghindari pemakaian ulang.

4. **Keamanan & Aturan Akses**:
   - Endpoint mutasi (change-password, change-phone) memerlukan JWT Bearer token dengan role `buyer` (`get_current_buyer` dependency).
   - Token dengan role selain `buyer` ditolak dengan `HTTP 401 (User is not a buyer)`.
   - Request mutasi tanpa token ditolak dengan `HTTP 401`.
   - Endpoint forgot/reset password via email bersifat **Public** tanpa autentikasi.

---

## 25. Integrasi WhatsApp Chatbot Management & Kontak Publik

1. **Pengambilan Nomor Dinamis (`fetch_whatsapp_number()`)**:
   - Sistem tidak lagi meng-hardcode nomor chatbot WhatsApp melainkan mengambil nomor aktif secara runtime via `GET {chatbot_url}/status` (timeout 5s).
   - Jika keadaan `tersambung` dan nomor tersedia, gunakan nomor live tersebut.
   - Jika chatbot offline/error/keadaan `terputus` atau `menunggu_scan`, sistem otomatis fallback ke konfigurasi `settings.CHATBOT_WA_NUMBER` (default: `"6287881273160"`).
   - Digunakan oleh alur verifikasi `start_wa_verification()` untuk membentuk deeplink `https://wa.me/{wa_number}?text=VERIFIKASI%20{nonce}`.

2. **Manajemen Admin WhatsApp (`/admin/whatsapp`)**:
   - `GET /admin/whatsapp/status`: Akses Admin/Owner (`require_admin_or_owner`) untuk memantau status koneksi chatbot. Meneruskan data realtime dari chatbot (`keadaan`: `"tersambung"` | `"menunggu_scan"` | `"terputus"`, `nomor`, `profile_name`).
   - `GET /admin/whatsapp/qr`: Akses khusus Owner (`require_owner`) untuk mengambil QR code autentikasi PNG dengan header `Cache-Control: no-store`.
   - `POST /admin/whatsapp/ganti-nomor`: Akses khusus Owner (`require_owner`) untuk mereset nomor. Sebelum memicu reset ke chatbot, sistem mengambil status nomor lama untuk dicatat pada log audit (siapa user, timestamp UTC, nomor lama) dan memanggil `POST {chatbot_url}/ganti-nomor` dengan timeout 70 detik (>= 65s). Setelah reset, chatbot masuk ke state `"menunggu_scan"`.

3. **Public Kontak Toko (`GET /public/kontak-toko`)**:
   - Endpoint public tanpa autentikasi untuk mengambil nomor WhatsApp aktif toko/chatbot.
   - Dilengkapi in-memory caching (`app_cache`) selama ±60 detik untuk mengurangi beban network HTTP ke chatbot.

---

## 26. Optimasi Query PostgreSQL, pg_trgm Trigram Indexing & ORM Eager Loading

1. **GIN Trigram Indexing (`pg_trgm`)**:
   - Ekstensi `pg_trgm` diaktifkan untuk mengoptimasi query pencarian teks substring (`ILIKE '%query%'`) pada kolom-kolom tabel `products`, `categories`, `users`, `buyers`, `customers`, `stock_items`, dan `suppliers`.
   - Menghindari full table scan dan menjaga latensi query sub-detik saat volume data membesar.

2. **Standardisasi Tipe Data Skema Database**:
   - Kolom deskripsi dan URL gambar distandarkan menggunakan tipe `TEXT` (menghindari limitasi buatan `VARCHAR(500)`).
   - Rating produk distandarkan menggunakan `Numeric(3, 2)` (menghindari binary rounding bug pada float).
   - Seluruh status boolean ditegaskan dengan `NOT NULL` dan default value.

3. **Pencegahan Masalah N+1 Query**:
   - Repository `Product`, `Purchase`, `Wishlist`, dan `Order` menggunakan `selectinload` untuk relasi bersarang (`category_rel`, `recipes.stock_item`, `price_histories`, `supplier`, `purchase_items.stock_item`, `customer`, `invoice.payments`).