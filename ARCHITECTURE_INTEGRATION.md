# System Architecture & Multi-Service Integration

Dokumen ini memetakan arsitektur multi-layanan, topologi jaringan, protokol komunikasi, serta kontrak integrasi antar-service dalam ekosistem **Toti Cakery**.

---

## 1. Topologi Sistem & Arsitektur Layanan

Ekosistem Toti Cakery terdiri dari **5 komponen utama** yang saling terhubung:

```mermaid
flowchart TB
    subgraph ClientLayer ["Client Layer"]
        BuyerWeb["🌐 Buyer Site (Vercel)"]
        AdminWeb["🖥️ Admin & Owner Dashboard (Vercel)"]
        WACustomer["📱 Pelanggan (WhatsApp App)"]
    end

    subgraph ServiceLayer ["Service & Gateway Layer"]
        ChatbotService["🤖 Chatbot AI Service (WhatsApp Gateway)"]
        BackendAPI["⚙️ Backend API Engine (FastAPI on Port 8000)"]
    end

    subgraph ExternalLayer ["External Infrastructure & Cloud"]
        MidtransPG["💳 Midtrans Payment Gateway (Core API)"]
        NeonDB[("🐘 PostgreSQL Database (Neon.tech)")]
    end

    %% Client to Services
    BuyerWeb -->|"HTTP REST + JWT (role: buyer)"| BackendAPI
    AdminWeb -->|"HTTP REST + JWT (role_level: 1/2/3)"| BackendAPI
    WACustomer <-->|"Pesan WhatsApp"| ChatbotService

    %% Chatbot to Backend
    ChatbotService -->|"HTTP REST (X-Service-Key)"| BackendAPI
    BackendAPI -.->|"Push Webhook Order Ready (X-Internal-Key)"| ChatbotService

    %% Backend to External
    BackendAPI <-->|"Async SQL (asyncpg)"| NeonDB
    BackendAPI -->|"Headless Charge (Basic Auth)"| MidtransPG
    MidtransPG -->|"Webhook Settlement (SHA-512 Signature)"| BackendAPI
```

---

## 2. Peran & Batasan Tanggung Jawab Komponen

| Komponen | Tanggung Jawab Utama | Tidak Bertanggung Jawab Atas |
| :--- | :--- | :--- |
| **Backend API Engine (FastAPI)** | - Single Source of Truth data transaksional.<br>- Validasi bisnis, kalkulasi HPP, optimasi stok (Optimistic Locking).<br>- Integrasi payment charge Midtrans & webhook settlement.<br>- RBAC internal & autentikasi buyer. | - Log percakapan teks bebas chatbot.<br>- Rendering antarmuka grafis (UI/UX).<br>- Integrasi langsung protokol WhatsApp Socket/Baileys. |
| **Chatbot AI Service** | - Natural Language Understanding (NLU/LLM).<br>- Manajemen sesi chat WhatsApp & state machine percakapan.<br>- Validasi format pesan pelanggan sebelum memanggil Backend.<br>- Notifikasi WhatsApp interaktif ke pelanggan. | - Perhitungan harga/diskon manual (harus ikut backend).<br>- Akses langsung ke database PostgreSQL (harus lewat API). |
| **Buyer Site (Frontend Web)** | - Katalog interaktif, cart, checkout via web.<br>- Registrasi akun & login via WhatsApp Deep Link OTP.<br>- Manajemen ulasan produk oleh pembeli. | - Validasi stok bahan baku (dilakukan oleh backend). |
| **Admin & Owner Dashboard** | - Visualisasi Laporan Keuangan (P&L), analitik penjualan.<br>- Master data: Produk, Resep (BOM), Stok Bahan Baku, Supplier, Purchasing.<br>- Manajemen akun internal & toggle penangan takeover. | - Logika settlement pembayaran otomatis. |
| **Midtrans Payment Gateway** | - Memproses pembayaran Virtual Account BCA dan QRIS.<br>- Mengirimkan callback notifikasi status pembayaran. | - Perubahan status inventori atau pesanan internal. |

---

## 3. Matriks Kredensial, Autentikasi & Secret Keys

| Nama Key / Header | Pengirim | Penerima | Kegunaan |
| :--- | :--- | :--- | :--- |
| `Authorization: Bearer <JWT>` | Buyer Web / Admin Web | Backend API | Autentikasi user. Payload berisi `user_id`, `role_level` (1=Owner, 2=Admin, 3=Staff) atau `role: "buyer"`. |
| `X-Service-Key` | Chatbot Service | Backend API | Mengotorisasi pemanggilan endpoint transaksional headless (`/orders`, `/customers`, `/payments`, `/reports/summary`). |
| `X-Internal-Key` | Backend API / Chatbot | Chatbot / Backend | Otorisasi webhook internal (misal: push notifikasi pesanan `ready` dari backend ke bot). |
| `MIDTRANS_SERVER_KEY` | Backend API | Midtrans API | Basic Auth saat melakukan `/charge` ke Midtrans Core API dan verifikasi SHA-512 webhook signature. |
| `CORS_ORIGINS` | Browser Clients | Backend API | Daftar domain yang diizinkan melakukan cross-origin request (`http://localhost:5173`, `http://127.0.0.1:5173`, `https://toti-cakery.vercel.app`). |
| `ENVIRONMENT` | Environment Config | Backend API | Lingkungan aplikasi (`development` / `production`). Menjadi guardrail keamanan otomatis. |
| `WA_VERIFICATION_MODE` | Environment Config | Backend API | Mode verifikasi WA (`mock` / `real`). Jika `ENVIRONMENT="production"`, selalu dipaksa bernilai `real`. |

---

## 4. Alur Event & Webhook Antar-Layanan

### A. Webhook Settlement Pembayaran (Midtrans $\rightarrow$ Backend)

```mermaid
sequenceDiagram
    autonumber
    actor Customer as Pelanggan
    participant Midtrans as Midtrans Core API
    participant Backend as Backend API
    participant DB as PostgreSQL (Neon)

    Customer->>Midtrans: Melakukan Pembayaran (VA BCA / QRIS)
    Midtrans->>Backend: POST /payments/notify (JSON Payload + signature_key)
    Backend->>Backend: Validasi SHA512(order_id + status_code + gross_amount + server_key)
    alt Signature Valid & Status Settlement
        Backend->>DB: Update Payment Status = 'Success'
        Backend->>DB: Hitung total pembayaran sukses pada invoice
        alt Total Terbayar >= Total Tagihan
            Backend->>DB: Invoice Status = 'paid', Order Status = 'in_process'
        else Pembayaran DP Sebagian
            Backend->>DB: Invoice Status = 'partial'
        end
        Backend-->>Midtrans: HTTP 200 {"status": "ok"}
    else Signature Tidak Cocok
        Backend-->>Midtrans: HTTP 400 Bad Request (Invalid Signature)
    end
```

---

### B. Push Notifikasi Kesiapan Pesanan (Backend $\rightarrow$ Chatbot $\rightarrow$ WhatsApp)

```mermaid
sequenceDiagram
    autonumber
    actor Admin as Admin / Owner
    participant Backend as Backend API
    participant Chatbot as Chatbot Service
    actor Customer as Pelanggan (WhatsApp)

    Admin->>Backend: PATCH /orders/{id}/status {"status": "ready"}
    Backend->>Backend: Update order status = 'ready' di database
    Backend->>Chatbot: POST {CHATBOT_URL}/webhook/internal/orders/{id}/ready (Header: X-Internal-Key)
    Chatbot->>Chatbot: Format pesan notifikasi ramah pelanggan
    Chatbot->>Customer: Kirim pesan WhatsApp: "Pesanan #INV-... sudah siap diambil/dikirim!"
    Chatbot-->>Backend: HTTP 200 OK
    Backend-->>Admin: HTTP 200 OK (OrderOut)
```

---

### C. WhatsApp Deep Link Verification (Buyer Web $\rightarrow$ WhatsApp $\rightarrow$ Backend)

```mermaid
sequenceDiagram
    autonumber
    actor Buyer as Calon Pembeli
    participant Web as Buyer Site
    participant Backend as Backend API
    participant Chatbot as Chatbot Service

    Buyer->>Web: Input Nomor WhatsApp (Contoh: 08123456789)
    Web->>Backend: POST /auth/verify/wa/start {"phone_number": "08123456789"}
    Backend->>Backend: Generate 6-char nonce (misal: "X8K9LP") & Simpan di DB (expired 10m)
    Backend-->>Web: {"nonce": "X8K9LP", "deeplink": "https://wa.me/...text=VERIFIKASI%20X8K9LP"}
    Web->>Buyer: Buka Link WhatsApp & Mulai Polling GET /auth/verify/wa/status?nonce=X8K9LP
    Buyer->>Chatbot: Kirim Pesan WA: "VERIFIKASI X8K9LP"
    Chatbot->>Backend: POST /auth/verify/wa/confirm {"nonce": "X8K9LP", "sender_phone": "628123456789"} (X-Service-Key)
    Backend->>Backend: Validasi nomor pengirim == nomor pendaftaran
    Backend->>Backend: Set otp.is_verified = True, generate verify_token (UUID)
    Backend-->>Chatbot: HTTP 200 OK
    Chatbot->>Buyer: Balas WA: "Nomor Anda berhasil diverifikasi! Silakan lanjutkan di web."
    Web->>Backend: Polling GET /auth/verify/wa/status?nonce=X8K9LP
    Backend-->>Web: {"status": "verified", "verify_token": "a1b2-c3d4-..."}
    Web->>Backend: POST /auth/buyer/register {"verify_token": "a1b2-c3d4-...", ...}
    Backend-->>Web: HTTP 201 Created (JWT Token Buyer)
```

---

## 5. Rencana Skalabilitas & Arsitektur Masa Depan (*Roadmap*)

1. **Background Task & Asynchronous Message Broker**:
   - Jika volume notifikasi webhook meningkat, transisi pengiriman notifikasi dari direct HTTP request menjadi *event queue* berbasis **Redis + ARQ / Celery** untuk mencegah latency spike pada proses checkout.
2. **Object Storage Cloud (AWS S3 / Cloudflare R2)**:
   - Saat ini gambar produk disimpan pada direktori `/static/products/` lokal. Untuk deployment serverless berskala besar, disiapkan adapter upload ke S3 / Cloudflare R2 dengan CDN Cloudflare.
3. **Database Connection Pooling**:
   - Pemanfaatan koneksi pool asyncpg yang dioptimalkan untuk Neon Serverless Postgres via PgBouncer pooling mode.
