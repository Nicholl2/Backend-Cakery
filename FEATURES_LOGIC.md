# Business Logic & Automations

Dokumentasi lengkap logika bisnis, aturan validasi, dan otomasi alur kerja Backend Toti Cakery.

---

## 1. Pembuatan Order, Reservasi Stok & Optimistic Concurrency Control

Alur kerja saat endpoint `POST /orders` dipanggil oleh Chatbot atau Client:
1. **Validasi Tagihan Aktif**:
   Sistem memeriksa apakah customer memiliki order yang belum lunas (`InvoiceStatusEnum.unpaid` atau `partial`). Jika ada, pembuatan order baru ditolak dengan HTTP `409 Conflict`.
2. **Validasi Produk & Minimum Order**:
   - Produk harus berstatus `is_active = True` dan sudah memiliki `harga_jual`.
   - Kuantitas pesanan (`jumlah`) tidak boleh kurang dari `minimum_order` produk.
3. **Kalkulasi Kebutuhan Bahan Baku (Bill of Materials)**:
   - Untuk setiap produk dalam pesanan, sistem mengalikan kuantitas pesanan dengan takaran bahan baku di tabel `recipes`:
     $$\text{total\_needed} = \text{jumlah pesanan} \times \text{jumlah\_dibutuhkan}$$
   - Sistem menjumlahkan kebutuhan per `stock_item_id` dan memvalidasi apakah `stok_tersedia >= total_needed`.
4. **Pengurangan Stok dengan Optimistic Locking**:
   - Pengurangan stok dieksekusi dengan query berfilter versi:
     ```sql
     UPDATE stock_items 
     SET stok_tersedia = stok_tersedia - :total_needed, version = version + 1
     WHERE id = :stock_id AND version = :current_version;
     ```
   - Jika `rowcount == 0` (terjadi modifikasi bersamaan oleh transaksi lain), transaksi langsung di-rollback dan melempar HTTP `400 Bad Request` (Concurrency Failure) untuk mencegah overselling.
5. **Snapshot HPP & Pembuatan Invoice**:
   - Nilai HPP produk saat transaksi disimpan ke kolom `order_items.hpp_snapshot` untuk integritas audit laba kotor di masa mendatang.
   - Nomor invoice dibuat dengan format: `INV-YYYYMMDD-{order_id}` dengan status awal `unpaid`.

---

## 2. Pembatalan Order & Pemulihan Stok Bahan Baku

Alur kerja saat endpoint `POST /orders/{order_id}/cancel` dipanggil:
1. **Proteksi Status Invoice**:
   Hanya pesanan yang status invoice-nya masih murni `unpaid` yang dapat dibatalkan otomatis. Pesanan yang sudah dibayar penuh (`paid`) atau memiliki DP (`partial`) ditolak dengan HTTP `409 Conflict`.
2. **Restorasi Stok Bahan Baku**:
   - Sistem menghitung kembali kuantitas bahan baku dari resep produk yang ada di `order_items`.
   - Mengembalikan kuantitas ke `stock_items.stok_tersedia` menggunakan Optimistic Locking (`version = version + 1`).
3. **Pembaruan Status**:
   Status order diubah menjadi `cancelled`.

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
3. **Pencatatan Record Pembayaran**:
   - Membuat record baru di tabel `payments` dengan status awal `Pending` dan tipe `DP` atau `Final`.

---

## 4. Otomasi Webhook Settlement & Transisi Status Transaksi

Alur kerja saat Midtrans memanggil webhook listener `POST /payments/notify`:
1. **Verifikasi Integritas Request (SHA512 Signature Key)**:
   Backend menghitung hash SHA-512 dari kombinasi:
   $$\text{hash} = \text{SHA512}(\text{order\_id} + \text{status\_code} + \text{gross\_amount} + \text{midtrans\_server\_key})$$
   Jika signature tidak cocok, request ditolak dengan HTTP `400 Bad Request (Invalid Signature)`.
2. **Pemetaan Status Transaksi**:
   - `settlement` / `capture` $\rightarrow$ `PaymentStatusEnum.success`
   - `deny` / `cancel` / `expire` $\rightarrow$ `PaymentStatusEnum.failed`
   - `pending` $\rightarrow$ `PaymentStatusEnum.pending`
3. **Kalkulasi Akumulasi & Transisi Status Invoice/Order**:
   Jika status payment berubah menjadi `Success`:
   - Sistem menghitung total akumulasi pembayaran sukses:
     $$\text{total\_success} = \sum (\text{Payment.jumlah\_bayar}) \quad \text{dimana status} = \text{'Success'}$$
   - **Kondisi Lunas Penuh**:
     Jika $\text{total\_success} \ge \text{invoice.total\_tagihan}$:
     - `invoice.status` diubah menjadi `paid`.
     - `order.status` otomatis bertransisi dari `pending` menjadi `in_process` (memulai produksi).
   - **Kondisi Uang Muka (DP)**:
     Jika $\text{total\_success} < \text{invoice.total\_tagihan}$:
     - `invoice.status` diubah menjadi `partial`.

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