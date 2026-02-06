from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/"
    database_url: str = "postgresql://vos_user:vos_password@localhost:5432/vos_database"
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()