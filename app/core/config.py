from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings from environment variables"""
    # Menggunakan huruf kecil agar standar pemetaan environment aman
    database_url: str
    secret_key: str = "your-secret-key-change-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 480
    service_api_key: str = "change-this-service-key"
    chatbot_url: str = "http://localhost:8000"
    chatbot_internal_key: str = ""
    midtrans_server_key: str = ""
    midtrans_is_production: bool = False
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173,https://toti-cakery.vercel.app"
    chatbot_wa_number: str = ""
    environment: str = "development"
    wa_verification_mode: str = "mock"  # "mock" | "real"
    cloudinary_cloud_name: str = ""
    cloudinary_api_key: str = ""
    cloudinary_api_secret: str = ""

    @model_validator(mode="after")
    def enforce_production_security(self) -> "Settings":
        if self.environment.lower() == "production":
            self.wa_verification_mode = "real"
        return self

    @property
    def ENVIRONMENT(self) -> str:
        return self.environment

    @property
    def WA_VERIFICATION_MODE(self) -> str:
        if self.environment.lower() == "production":
            return "real"
        return self.wa_verification_mode

    @property
    def midtrans_api_url(self) -> str:
        return "https://api.midtrans.com/v2" if self.midtrans_is_production else "https://api.sandbox.midtrans.com/v2"

    # Menggunakan model_config (Standar Pydantic v2 untuk membaca .env)
    model_config = SettingsConfigDict(
        env_file=".env", 
        env_file_encoding="utf-8", 
        extra="ignore"
    )


settings = Settings()