import logging
from pydantic import Field, AliasChoices, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Application settings from environment variables"""
    # Menggunakan huruf kecil agar standar pemetaan environment aman
    database_url: str
    secret_key: str = "your-secret-key-change-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    service_api_key: str = "change-this-service-key"
    service_orders_read_key: str = ""
    service_orders_cancel_key: str = ""
    service_customers_key: str = ""
    service_refund_key: str = ""
    service_payment_status_key: str = ""
    service_financial_summary_key: str = ""
    service_owner_numbers_key: str = ""
    service_settlement_key: str = ""
    service_wa_verification_key: str = ""
    chatbot_url: str = "http://localhost:8000"
    chatbot_internal_key: str = ""
    midtrans_server_key: str = ""
    midtrans_is_production: bool = False
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173,https://toti-cakery.vercel.app"
    chatbot_wa_number: str = "6287881273160"
    environment: str = Field(default="development", validation_alias=AliasChoices("environment", "app_env"))
    wa_verification_mode: str = "mock"  # "mock" | "real"
    cloudinary_cloud_name: str = ""
    cloudinary_api_key: str = ""
    cloudinary_api_secret: str = ""

    # SMTP / Email configuration
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_email: str = ""
    smtp_from_name: str = "Toti Cakery"
    smtp_use_tls: bool = True
    smtp_operational_inbox: str = ""
    email_outbox_poll_seconds: int = 5
    email_worker_enabled: bool = False
    email_outbox_max_attempts: int = 10
    email_outbox_lease_seconds: int = Field(default=300, ge=3)
    email_outbox_admin_token: str = ""

    @model_validator(mode="after")
    def enforce_production_security(self) -> "Settings":
        env_name = (self.environment or "").lower()
        if env_name == "production":
            self.wa_verification_mode = "real"
            if not self.smtp_host or not self.smtp_from_email:
                logger.warning(
                    "[CONFIG_WARNING] Production mode enabled but SMTP host/sender email is not configured. "
                    "Email outbox dispatches will fail until SMTP settings are provided."
                )
            if self.secret_key == "your-secret-key-change-in-production":
                raise ValueError("CRITICAL SECURITY: 'secret_key' wajib diganti dengan string rahasia kuat pada environment production!")
            if self.service_api_key == "change-this-service-key":
                raise ValueError("CRITICAL SECURITY: 'service_api_key' wajib diganti pada environment production!")
            
            # Filter out localhost and 127.0.0.1 from CORS in production
            cleaned_origins = [
                origin.strip() for origin in self.cors_origins.split(",")
                if origin.strip() and not ("localhost" in origin.lower() or "127.0.0.1" in origin.lower())
            ]
            if cleaned_origins:
                self.cors_origins = ",".join(cleaned_origins)
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
    def ACCESS_TOKEN_EXPIRE_MINUTES(self) -> int:
        return self.access_token_expire_minutes

    @property
    def CHATBOT_WA_NUMBER(self) -> str:
        return self.chatbot_wa_number or "6287881273160"

    @property
    def DATABASE_URL(self) -> str:
        return self.database_url

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
