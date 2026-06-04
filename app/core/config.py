from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings from environment variables"""
    # Menggunakan huruf kecil agar standar pemetaan environment aman
    database_url: str
    secret_key: str = "your-secret-key-change-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 480

    # Menggunakan model_config (Standar Pydantic v2 untuk membaca .env)
    model_config = SettingsConfigDict(
        env_file=".env", 
        env_file_encoding="utf-8", 
        extra="ignore" # Mengabaikan variabel lain di .env yang tidak dideklarasikan di sini
    )


settings = Settings()