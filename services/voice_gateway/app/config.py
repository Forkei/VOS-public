"""
Voice Gateway Configuration
Handles environment variables and application settings
"""

from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""

    # Service info
    SERVICE_NAME: str = "voice_gateway"
    SERVICE_VERSION: str = "1.0.0"

    # Authentication
    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_MINUTES: int = 60

    # Voice services - STT
    ASSEMBLYAI_API_KEY: str

    # TTS Provider Selection
    TTS_PROVIDER: str = "elevenlabs"  # Options: elevenlabs, cartesia

    # ElevenLabs
    ELEVENLABS_API_KEY: str
    ELEVENLABS_VOICE_ID: str = "21m00Tcm4TlvDq8ikWAM"  # Default voice (Rachel)

    # Cartesia
    CARTESIA_API_KEY: str = ""
    CARTESIA_VOICE_ID: str = "6ccbfb76-1fc6-48f7-b71d-91ac6298247b"

    # API Gateway
    API_GATEWAY_URL: str = "http://api_gateway:8000"

    # RabbitMQ
    RABBITMQ_URL: str = "amqp://guest:guest@rabbitmq:5672/"
    RABBITMQ_EXCHANGE: str = "vos_exchange"

    # PostgreSQL
    POSTGRES_HOST: str = "postgres"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_DB: str = "vos_database"

    # WebSocket
    WS_HEARTBEAT_INTERVAL: int = 30  # seconds
    WS_MESSAGE_QUEUE_SIZE: int = 100

    # Session settings
    SESSION_TIMEOUT: int = 300  # 5 minutes
    MAX_AUDIO_CHUNK_SIZE: int = 1048576  # 1MB

    # AssemblyAI settings
    ASSEMBLYAI_SAMPLE_RATE: int = 16000  # Default sample rate for audio
    ASSEMBLYAI_FORMAT_TURNS: bool = True  # Enable turn formatting
    ASSEMBLYAI_INTERIM_RESULTS: bool = True  # Enable interim results

    # ElevenLabs settings
    ELEVENLABS_MODEL: str = "eleven_turbo_v2"
    ELEVENLABS_STABILITY: float = 0.5
    ELEVENLABS_SIMILARITY_BOOST: float = 0.75
    ELEVENLABS_STYLE: float = 0.0

    # Cartesia settings (sonic-3 is fastest, sonic-english is highest quality English)
    CARTESIA_MODEL: str = "sonic-3"
    CARTESIA_VERSION: str = "2024-11-13"  # Use latest stable API version
    CARTESIA_OUTPUT_FORMAT: str = "wav"
    CARTESIA_ENCODING: str = "pcm_s16le"  # 16-bit PCM for broader compatibility
    CARTESIA_SAMPLE_RATE: int = 24000  # 24kHz balances quality and speed for calls
    CARTESIA_EMOTION: str = "neutral"  # Primary: neutral, angry, excited, content, sad, scared

    # Logging
    LOG_LEVEL: str = "INFO"

    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"  # Ignore extra env vars like GEMINI_API_KEY

    @property
    def postgres_url(self) -> str:
        """Get PostgreSQL connection URL"""
        return f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"


# Global settings instance
settings = Settings()
