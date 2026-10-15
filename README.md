# Toti Cakery - Backend API Gateway Engine

This repository serves as the core backend operational engine for the **Toti Cakery** system. It is built using **FastAPI** with a fully asynchronous architecture and **PostgreSQL 17** as the primary database management system.

The application implements a structured access control mechanism using a dynamic **Hierarchical Role-Based Access Control (RBAC)** framework.

---

## 🛠️ System Requirements

Before running this project, ensure the following software is installed on your machine:

* **Python 3.12+**
* **PostgreSQL 17+**
* **Homebrew** *(macOS / Apple Silicon users only)*
* **Postman** or **Insomnia** *(for API testing)*

---

## 📥 Installation & Local Setup

Follow these steps to run the project locally.

### 1. Clone the Repository

```bash
git clone https://github.com/nicholl2/backend-cakery.git
cd backend-cakery/Backend
```

---

### 2. Create and Activate Virtual Environment

Create a Python virtual environment:

```bash
python3 -m venv venv
```

Activate the virtual environment:

**macOS / Linux**

```bash
source venv/bin/activate
```

**Windows (PowerShell)**

```powershell
.\venv\Scripts\Activate.ps1
```

---

### 3. Install Dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

---

### 4. Setup PostgreSQL Database

#### Install PostgreSQL 17 (macOS)

```bash
brew install postgresql@17
```

Start the PostgreSQL service:

```bash
brew services start postgresql@17
```

Open the PostgreSQL CLI:

```bash
psql postgres
```

Inside the PostgreSQL shell, execute:

```sql
CREATE USER postgres WITH PASSWORD 'yourpassword' SUPERUSER;
CREATE DATABASE toti_cakery OWNER postgres;
```

Exit PostgreSQL:

```sql
\q
```

For a newly created, empty database, set `DATABASE_URL` in `.env` and initialize the current schema before starting the API:

```bash
python -m app.db_bootstrap --confirm-empty
```

This command refuses any database that already has tables or custom enum types. It creates the schema from the current SQLAlchemy models, applies extensions/indexes, and stamps Alembic at the current head (`20261015_01`). Do not run `alembic upgrade head` directly against a blank database: the historical migration chain starts from an existing application schema, not from a blank PostgreSQL database.

For a database that already has an `alembic_version` row, back it up and use `alembic upgrade head` after reviewing the pending revisions. For an existing application database without an Alembic revision, do not run `upgrade head` or blindly stamp head. Run the read-only preflight tool to verify schema and data prerequisites, including the exact unique stock-name index and ledger balances. It fails closed if refund migration objects already exist because their schema/data cannot safely be adopted by the historical refund revision:

```bash
# 1. Read-only audit (stops if any prerequisite is unmet):
python -m app.db_preflight

# 2. Stamp 20261005_01 (only if preflight passes with 0 errors):
python -m app.db_preflight --stamp

# 3. Apply subsequent revisions (refund transactions, outbox, and payment order references):
alembic upgrade head
```

If the baseline comparison fails, stop and prepare a database-specific reconciliation plan; never reset the existing database.

API startup note: The API lifespan no longer performs runtime DDL migrations during startup. It verifies connectivity and schema presence, and only runs idempotent initial master data seeding if the `roles` table is empty. Critical initialization failures cause the API to fail fast rather than starting in a degraded state.

---

### 5. Configure Environment Variables

Create a `.env` file in the project root directory and add the following configuration:

```env
DATABASE_URL=your-database-url
SECRET_KEY=your-super-secret-key-change-this-in-production
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=60
SERVICE_API_KEY=your-chatbot-service-key
CHATBOT_URL=http://localhost:8000
CHATBOT_INTERNAL_KEY=your-internal-key
CHATBOT_WA_NUMBER=6281234567890
MIDTRANS_SERVER_KEY=SB-Mid-server-...
MIDTRANS_IS_PRODUCTION=...
CORS_ORIGINS=http://your-cors-origin
ENVIRONMENT=development
WA_VERIFICATION_MODE=mock
```

> **Catatan Pengujian WA & Keamanan Produksi:**
> - `ENVIRONMENT`: `"development"` | `"production"`
> - `WA_VERIFICATION_MODE`: `"mock"` | `"real"` (Jika `ENVIRONMENT="production"`, sistem secara otomatis memaksakan mode `"real"` untuk menjaga keamanan verifikasi).
> - Default `CORS_ORIGINS` telah mendukung frontend produksi di Vercel (`https://toti-cakery.vercel.app`) dan development local (`localhost:5173`).

---

### 6. Seed Initial Master Data (Optional / Automatic on Startup)

Sistem akan otomatis melakukan seeding data dasar saat startup jika tabel `roles` masih kosong. Anda juga dapat menjalankan seeder secara manual melalui CLI:

```bash
python -m app.seed_data
```

Default credentials yang dibuat:
- **Owner (Admin Site)**: `imeng` / `Admin_123` (Email: `imeng@toticakery.com` | HP/WA: `08111111111`)
- **Admin (Admin Site)**: `ameng` / `Admin_123` (Email: `ameng@toticakery.com` | HP/WA: `08222222222`)
- **Staff (Admin Site)**: `smeng` / `Staff_123` (Email: `smeng@toticakery.com` | HP/WA: `08333333333`)
- **Buyer (Buyer Site)**: `aceng@gmail.com` / `Aceng_123` (HP/WA: `08123456789`)
*(Catatan: Login akun internal di Frontend mendukung login via Username, Email, ataupun Nomor HP/WA)*

---

### 7. Run the FastAPI Server

```bash
uvicorn app.main:app --reload
```

The API server will be available at:

```text
http://127.0.0.1:8000
```

---

## 🧪 Testing Suite

Proyek ini menggunakan **Pytest** dan **pytest-asyncio** untuk pengujian unit, integrasi, dan hardening sistem secara asinkronus. Seluruh berkas pengujian berada di dalam direktori `tests/`.

### Menjalankan Seluruh Test Suite

```bash
pytest
```

Atau secara spesifik per file:

```bash
pytest tests/test_hardening.py
pytest tests/test_seller_orders.py
pytest tests/test_avatar_upload.py
pytest tests/test_master_data.py
pytest tests/test_buyer_orders_payments.py
pytest tests/test_imports.py
```

### Struktur Pengujian (`tests/`)

* `test_hardening.py`: Pengujian Concurrency, Optimistic Locking auto-retry, Idempotency webhook Midtrans, Row-Level Locking (`FOR UPDATE`), dan State Machine.
* `test_seller_orders.py`: Pengujian Seller Orders, custom order dari katalog dengan aksesoris stok, kalkulasi tagihan/pembayaran, dan pemulihan stok bahan serta aksesoris saat dibatalkan.
* `test_avatar_upload.py`: Pengujian upload gambar ke Cloudinary via direct memory stream, validasi MIME/ekstensi, dan batasan ukuran 5 MB.
* `test_master_data.py`: Pengujian CRUD Supplier, StockItem, Product, Recipe, Review, dan seeder akun Buyer.
* `test_buyer_orders_payments.py`: Pengujian checkout pesanan Buyer dan validasi alur pembayaran via JWT & Service Key.
* `test_imports.py`: Verifikasi integritas registrasi seluruh model, schema, service, dan 90+ router endpoint.

---

## Order, payment, and refund lifecycle

- New customer orders start as `pending`; creating an order records each ingredient deduction as an `ORDER` stock movement. Seller in-app notifications and seller email outbox entries are committed with supported customer order creation paths.
- Midtrans `settlement` updates the payment to `Success` and the invoice to `partial` or `paid`; the order remains `pending` until a seller starts production. Midtrans `capture` counts as success only when `fraud_status` is `accept`.
- An unpaid order cancellation changes the order to `cancelled`. Stock is reversed from its original `ORDER` movements, and a `REVERSAL` movement prevents repeat cancellation/refund paths from restoring it twice. Current recipes are not used for reversal.
- A refund request cancels the order while refund confirmation is pending. The invoice/order become `refunded` when all settled funds are refunded, or when a seller records a completed manual refund. Failed internal Midtrans webhook processing returns 5xx so the gateway can retry; invalid signatures and unknown test orders are acknowledged without applying changes.
- Important buyer email events are written to the transactional email outbox and retried by the email worker. Keep `EMAIL_WORKER_ENABLED=false` by default; enable it only on a persistent deployment after SMTP and database readiness checks.

## 📚 API Documentation

FastAPI automatically generates interactive API documentation.

* **Swagger UI**
  `http://127.0.0.1:8000/docs`

* **ReDoc**
  `http://127.0.0.1:8000/redoc`

---

## 🏗️ Tech Stack

* **FastAPI**
* **PostgreSQL 17**
* **SQLAlchemy (Async)**
* **Alembic**
* **Pydantic**
* **JWT Authentication**
* **Hierarchical RBAC**
