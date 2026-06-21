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

---

### 5. Configure Environment Variables

Create a `.env` file in the project root directory and add the following configuration:

```env
DATABASE_URL=postgresql+asyncpg://postgres:yourpassword
SECRET_KEY=your-super-secret-key-change-this-in-production
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=480
```

---

### 6. Run the FastAPI Server

```bash
uvicorn app.main:app --reload
```

The API server will be available at:

```text
http://127.0.0.1:8000
```

---

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
