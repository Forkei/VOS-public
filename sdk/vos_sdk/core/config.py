"""
Configuration management for VOS agents.

Handles loading environment variables and providing configuration to agents.
"""

import os
from typing import Optional
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class AgentConfig:
    """
    Configuration container for VOS agents.

    Combines shared infrastructure settings (from environment)
    with agent-specific identity (from code).
    """

    # Agent Identity (set in code)
    agent_name: str
    agent_display_name: str

    # RabbitMQ Configuration (from environment)
    rabbitmq_host: str
    rabbitmq_port: int
    rabbitmq_user: str
    rabbitmq_password: str
    rabbitmq_vhost: str

    # Database Configuration (from environment)
    database_host: str
    database_port: int
    database_name: str
    database_user: str
    database_password: str

    # Weaviate Configuration (from environment)
    weaviate_host: str
    weaviate_port: int
    weaviate_scheme: str

    # API Gateway Configuration (from environment)
    api_gateway_host: str
    api_gateway_port: int
    api_gateway_scheme: str

    # LLM Configuration (from environment)
    gemini_api_key: Optional[str]

    # Logging Configuration (from environment)
    log_level: str
    log_format: str

    # Health Check Configuration (from environment)
    health_check_port: int
    health_check_path: str

    # Agent Processing Configuration (from environment)
    agent_check_interval_seconds: float

    # Conversation Memory Configuration (from environment)
    max_conversation_messages: int  # 0 = unlimited
    message_history_retrieval_limit: int  # How many messages to retrieve from DB (0 = all)

    @classmethod
    def from_env(cls, agent_name: str, agent_display_name: str) -> "AgentConfig":
        """
        Create configuration from environment variables.

        Args:
            agent_name: Unique identifier for the agent (e.g., "weather_agent")
            agent_display_name: Human-readable name for the agent (e.g., "Weather Service")

        Returns:
            AgentConfig instance with all settings loaded

        Raises:
            ValueError: If required environment variables are missing
        """

        def get_env(key: str, default: Optional[str] = None, required: bool = True) -> Optional[str]:
            """Helper to get environment variable with validation."""
            value = os.getenv(key, default)
            if required and value is None:
                raise ValueError(f"Required environment variable '{key}' is not set")
            return value

        def get_env_int(key: str, default: Optional[int] = None, required: bool = True) -> Optional[int]:
            """Helper to get integer environment variable."""
            value = get_env(key, str(default) if default is not None else None, required)
            if value is not None:
                try:
                    return int(value)
                except ValueError:
                    raise ValueError(f"Environment variable '{key}' must be an integer, got: {value}")
            return None

        def get_env_float(key: str, default: Optional[float] = None, required: bool = True) -> Optional[float]:
            """Helper to get float environment variable."""
            value = get_env(key, str(default) if default is not None else None, required)
            if value is not None:
                try:
                    return float(value)
                except ValueError:
                    raise ValueError(f"Environment variable '{key}' must be a float, got: {value}")
            return None

        config = cls(
            # Agent Identity
            agent_name=agent_name,
            agent_display_name=agent_display_name,

            # RabbitMQ
            rabbitmq_host=get_env("RABBITMQ_HOST", "rabbitmq"),
            rabbitmq_port=get_env_int("RABBITMQ_PORT", 5672),
            rabbitmq_user=get_env("RABBITMQ_USER", "guest"),
            rabbitmq_password=get_env("RABBITMQ_PASSWORD", "guest"),
            rabbitmq_vhost=get_env("RABBITMQ_VHOST", "/"),

            # Database
            database_host=get_env("DATABASE_HOST", "postgres"),
            database_port=get_env_int("DATABASE_PORT", 5432),
            database_name=get_env("DATABASE_NAME", "vos_db"),
            database_user=get_env("DATABASE_USER", "postgres"),
            database_password=get_env("DATABASE_PASSWORD", "postgres"),

            # Weaviate
            weaviate_host=get_env("WEAVIATE_HOST", "weaviate"),
            weaviate_port=get_env_int("WEAVIATE_PORT", 8080),
            weaviate_scheme=get_env("WEAVIATE_SCHEME", "http"),

            # API Gateway
            api_gateway_host=get_env("API_GATEWAY_HOST", "api_gateway"),
            api_gateway_port=get_env_int("API_GATEWAY_PORT", 8000),
            api_gateway_scheme=get_env("API_GATEWAY_SCHEME", "http"),

            # LLM (required - all agents use LLM)
            gemini_api_key=get_env("GEMINI_API_KEY"),

            # Logging
            log_level=get_env("LOG_LEVEL", "INFO"),
            log_format=get_env("LOG_FORMAT", "json"),

            # Health Check
            health_check_port=get_env_int("HEALTH_CHECK_PORT", 8080),
            health_check_path=get_env("HEALTH_CHECK_PATH", "/health"),

            # Agent Processing
            agent_check_interval_seconds=get_env_float("AGENT_CHECK_INTERVAL_SECONDS", 0.25),

            # Conversation Memory - allow per-agent override or use global default
            max_conversation_messages=get_env_int(
                f"{agent_name.upper()}_MAX_CONVERSATION_MESSAGES",
                get_env_int("MAX_CONVERSATION_MESSAGES", 0, required=False),
                required=False
            ),

            # Message History Retrieval Limit - how many messages to load from DB
            # Default to 500 if not specified (reasonable limit for most conversations)
            message_history_retrieval_limit=get_env_int(
                f"{agent_name.upper()}_MESSAGE_HISTORY_RETRIEVAL_LIMIT",
                get_env_int("MESSAGE_HISTORY_RETRIEVAL_LIMIT", 500, required=False),
                required=False
            ),
        )

        logger.info(f"Loaded configuration for agent: {agent_display_name} ({agent_name})")
        return config

    @property
    def queue_name(self) -> str:
        """Generate the RabbitMQ queue name for this agent."""
        return f"{self.agent_name}_queue"

    @property
    def rabbitmq_url(self) -> str:
        """Generate the full RabbitMQ connection URL."""
        return (
            f"amqp://{self.rabbitmq_user}:{self.rabbitmq_password}@"
            f"{self.rabbitmq_host}:{self.rabbitmq_port}/{self.rabbitmq_vhost}"
        )

    @property
    def database_url(self) -> str:
        """Generate the full PostgreSQL connection URL."""
        return (
            f"postgresql://{self.database_user}:{self.database_password}@"
            f"{self.database_host}:{self.database_port}/{self.database_name}"
        )

    @property
    def weaviate_url(self) -> str:
        """Generate the full Weaviate connection URL."""
        return f"{self.weaviate_scheme}://{self.weaviate_host}:{self.weaviate_port}"

    @property
    def api_gateway_url(self) -> str:
        """Generate the full API Gateway URL."""
        return f"{self.api_gateway_scheme}://{self.api_gateway_host}:{self.api_gateway_port}"

    def setup_logging(self) -> None:
        """Configure logging based on settings."""
        level = getattr(logging, self.log_level.upper(), logging.INFO)

        if self.log_format == "json":
            import json

            class JsonFormatter(logging.Formatter):
                def __init__(self, agent_name, agent_display_name):
                    super().__init__()
                    self.agent_name = agent_name
                    self.agent_display_name = agent_display_name

                def format(self, record):
                    log_obj = {
                        "timestamp": self.formatTime(record),
                        "level": record.levelname,
                        "agent_name": self.agent_name,
                        "agent_display_name": self.agent_display_name,
                        "message": record.getMessage(),
                        "module": record.module,
                        "function": record.funcName,
                    }
                    if record.exc_info:
                        log_obj["exception"] = self.formatException(record.exc_info)
                    return json.dumps(log_obj)

            formatter = JsonFormatter(self.agent_name, self.agent_display_name)
        else:
            formatter = logging.Formatter(
                f'[%(asctime)s] [{self.agent_display_name}] %(levelname)s - %(message)s'
            )

        handler = logging.StreamHandler()
        handler.setFormatter(formatter)

        root_logger = logging.getLogger()
        root_logger.setLevel(level)
        root_logger.handlers = [handler]

        # Suppress noisy third-party loggers
        logging.getLogger('pika').setLevel(logging.WARNING)
        logging.getLogger('httpx').setLevel(logging.WARNING)
        logging.getLogger('httpcore').setLevel(logging.WARNING)
        logging.getLogger('werkzeug').setLevel(logging.WARNING)
        logging.getLogger('urllib3').setLevel(logging.WARNING)

        logger.info(f"Logging configured: level={self.log_level}, format={self.log_format}")