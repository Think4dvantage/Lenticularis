#Import libraries (yaml, pydantic, pathlib for file handling)
import yaml
import os
from pydantic import BaseModel
from typing import Optional
from pathlib import Path

class InfluxDBConfig(BaseModel):
    enabled: bool = True
    url: Optional[str] = None
    token: Optional[str] = None
    org: Optional[str] = None
    bucket: Optional[str] = None
    timeout: int = 10000

class CollectorConfig(BaseModel):
    enabled: bool = True
    name: str = "meteoswiss"
    interval_minutes: int = 10
    config: Optional[dict] = None


class ForecastCollectorConfig(BaseModel):
    name: str = "open-meteo"
    enabled: bool = True
    interval_minutes: int = 60
    horizon_hours: int = 120
    config: Optional[dict] = None


class DatabaseConfig(BaseModel):
    path: str = "data/lenticularis.db"

class AuthConfig(BaseModel):
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 30

class LoggingConfig(BaseModel):
    level: str = "debug"
    format: str = "json"
    file: str = "logs/lenticularis.log"

class APIConfig(BaseModel):
    host: str = "localhost"
    port: int = 1337
    reload: bool = False

class OllamaConfig(BaseModel):
    url: str = "http://localhost:11434"
    model: str = "qwen2.5:3b"
    enabled: bool = True
    timeout_seconds: int = 60


class StationDedupConfig(BaseModel):
    # Two stations closer than this (metres) are merged into one virtual station.
    # 300 m covers typical paragliding sites where sensors from different networks
    # are placed at different spots on the same launch field.
    distance_m: float = 300.0


class GoogleOAuthConfig(BaseModel):
    enabled: bool = False
    client_id: str = ""
    client_secret: str = ""


class FacebookOAuthConfig(BaseModel):
    enabled: bool = False
    client_id: str = ""
    client_secret: str = ""


class OAuthConfig(BaseModel):
    base_url: str = ""   # e.g. "https://lenti.lg4.ch" — required when running behind a reverse proxy
    google: GoogleOAuthConfig = GoogleOAuthConfig()
    facebook: FacebookOAuthConfig = FacebookOAuthConfig()


class MainConfig(BaseModel):
    influxdb: InfluxDBConfig
    collectors: list[CollectorConfig]
    forecast_collectors: list[ForecastCollectorConfig] = []
    database: DatabaseConfig
    auth: AuthConfig = AuthConfig()
    logging: LoggingConfig
    api: APIConfig
    ollama: OllamaConfig = OllamaConfig()
    station_dedup: StationDedupConfig = StationDedupConfig()
    oauth: OAuthConfig = OAuthConfig()


#Module vars
_config: Optional[MainConfig] = None

#Create function load_config():
#   - Find and open config.yaml file
#   - Parse YAML into Python dictionary
#   - Pass dictionary to MainConfig Pydantic model (validates automatically)
#   - Return validated config object
#   - Handle errors (file not found, invalid YAML, validation errors)
def load_config(config_path: Optional[str] = None):
    if config_path is None:
        env_path = os.getenv("CONFIG_PATH", "")
        possible_paths = [
            Path(env_path) if env_path else None,
            Path.cwd() / "config.yml",
            Path("/etc/lenticularis/config.yml")
        ]

        for path in possible_paths:
            if path and path.exists():
                config_path = path
                break
        
        if config_path is None:
            raise FileNotFoundError("No config.yml found in expected Locations")
    config_path = Path(config_path)

    try:
        with open(config_path, 'r') as file:
            data = yaml.safe_load(file)

        return MainConfig.model_validate(data)
    
    except FileNotFoundError:
        raise FileNotFoundError(f"Config File not found: {config_path}")
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML in config file: {e}")
    except Exception as e:
        raise ValueError(f"Config valiadation failed: {e}")

#Create singleton pattern (load config once, reuse everywhere)
def get_config() -> MainConfig:
    global _config

    if _config is None:
        _config = load_config()

    return _config