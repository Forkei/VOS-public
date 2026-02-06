"""
Twilio Gateway Configuration
Handles environment variables and application settings
"""

from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""

    # Service info
    SERVICE_NAME: str = "twilio_gateway"
    SERVICE_VERSION: str = "1.0.0"

    # Twilio Configuration
    TWILIO_ACCOUNT_SID: str
    TWILIO_AUTH_TOKEN: str
    TWILIO_PHONE_NUMBER: str  # E.164 format (+1234567890)

    # Webhook Configuration
    WEBHOOK_BASE_URL: str = "https://api.jarvos.dev"

    # Internal Services
    API_GATEWAY_URL: str = "http://api_gateway:8000"
    VOICE_GATEWAY_URL: str = "http://voice_gateway:8100"

    # RabbitMQ
    RABBITMQ_URL: str = "amqp://guest:guest@rabbitmq:5672/"
    RABBITMQ_EXCHANGE: str = "vos_exchange"

    # PostgreSQL (defaults must match docker-compose.yml)
    POSTGRES_HOST: str = "postgres"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "vos_user"
    POSTGRES_PASSWORD: str  # Required - no default for security
    POSTGRES_DB: str = "vos_database"

    # Security Settings
    # NEVER set to true in production - only for local development without ngrok
    TWILIO_SKIP_SIGNATURE_VALIDATION: bool = False

    # Audio Settings
    TWILIO_SAMPLE_RATE: int = 8000  # Twilio uses 8kHz mulaw
    VOS_SAMPLE_RATE: int = 16000  # VOS pipeline uses 16kHz PCM

    # Session settings
    MAX_CONCURRENT_CALLS: int = 10
    CALL_TIMEOUT: int = 3600  # 1 hour max call duration

    # Logging
    LOG_LEVEL: str = "INFO"

    class Config:
        env_file = ".env"
        case_sensitive = True

    @property
    def postgres_url(self) -> str:
        """Get PostgreSQL connection URL"""
        return f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"


# Global settings instance
settings = Settings()
