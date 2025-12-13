"""
Core configuration management for Lenticularis
Uses Pydantic for type-safe settings
"""
from pydantic_settings import BaseSettings
from typing import Optional
from pathlib import Path


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""
    
    # Application
    APP_NAME: str = "Lenticularis"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    
    # Database - SQLite
    SQLITE_DB_PATH: str = "./data/lenticularis.db"
    
    # Database - InfluxDB
    INFLUXDB_URL: str = "http://localhost:8086"
    INFLUXDB_TOKEN: str = ""
    INFLUXDB_ORG: str = "lenticularis"
    INFLUXDB_BUCKET: str = "weather_data"
    
    # Data Collection
    COLLECTOR_INTERVAL_SECONDS: int = 600  # 10 minutes
    COLLECTOR_ENABLED: bool = True
    
    # API Keys (for weather sources)
    HOLFUY_API_KEY: Optional[str] = None
    
    # Alerts
    SIGNAL_ENABLED: bool = False
    SIGNAL_PHONE_NUMBER: Optional[str] = None
    
    DISCORD_ENABLED: bool = False
    DISCORD_WEBHOOK_URL: Optional[str] = None
    
    TELEGRAM_ENABLED: bool = False
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_CHAT_ID: Optional[str] = None
    
    # Logging
    LOG_LEVEL: str = "INFO"
    LOG_FILE: Optional[str] = "./logs/lenticularis.log"
    
    class Config:
        env_file = ".env"
        case_sensitive = True


# Global settings instance
settings = Settings()


def get_settings() -> Settings:
    """Dependency injection for FastAPI"""
    return settings
