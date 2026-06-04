# Toti Cakery - Backend API Gateway Engine 🎂

This repository is the core backend operational engine for Toti Cakery's main system. It is built using FastAPI with a pure asynchronous architecture and PostgreSQL 17 as the primary DBMS. This system implements structured access rights handling using a dynamic Hierarchical Role-Based Access Control (RBAC) framework.

---

## 🛠️ System Requirements & Installation Prerequisites (Mac Silicon / Windows)

Before running the project, make sure the following software is installed on your local device:

1. **Python 3.12+**
2. **PostgreSQL 17+**
3. **Homebrew** (MacOS/Apple Silicon users only)
4. **Postman** or **Insomnia** (For endpoint testing)

---

## 📋 Setup Steps from Scratch (Local)

Follow the instructions below step-by-step via terminal (Terminal on Mac or PowerShell/CMD on Windows):

### 1. Clone Repository & Change Directories
```bash
git clone [https://github.com/nicholl2/backend-cakery.git](https://github.com/nicholl2/backend-cakery.git)
cd backend-cakery/Backend

### 2. Setup Virtual Environment (venv)**
python3 -m venv venv

# Mac/Linux
source venv/bin/activate

# Windows (PowerShell)
.\venv\Scripts\Activate.ps1

### 3. Installing Library Dependencies**
pip install --upgrade pip
pip install -r requirements.txt

### 4. Setup Local PostgreSQL 17 Database**
# Install Postgres 17
brew install postgresql@17

# Run service background
brew services start postgresql@17

# Enter CLI PostgreSQL global
psql postgres

# In the postgres=# prompt, run this SQL query to setup the Toti database:
CREATE USER postgres WITH PASSWORD 'yourpassword' SUPERUSER;
CREATE DATABASE toti_cakery OWNER postgres;
\q

### 5. Environment (.env) File Configuration**
DATABASE_URL=copy-your-url
SECRET_KEY=your-super-secret-key-change-this-in-production-
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=480

### 6. Run FastAPI Server**
uvicorn app.main:app --reload

---

Access to 127.0.0.1:8000/docs
