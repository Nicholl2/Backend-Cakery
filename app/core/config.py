from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings from environment variables"""
    # Menggunakan huruf kecil agar standar pemetaan environment aman
    database_url: str
    secret_key: str = "your-secret-key-change-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 480
    service_api_key: str = "change-this-service-key"
    chatbot_url: str = "http://localhost:8001"
    midtrans_server_key: str = ""
    midtrans_is_production: bool = False

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