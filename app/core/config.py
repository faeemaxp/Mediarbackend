import os
from pydantic_settings import BaseSettings
from functools import lru_cache
from typing import Optional

class Settings(BaseSettings):
    MONGODB_URL: str = "mongodb://localhost:27017"
    DATABASE_NAME: str = "mediaradar"
    ADMIN_TOKEN: str = "supersecret"
    GEMINI_API_KEY: Optional[str] = None
    
    # Discord Webhooks
    DISCORD_WEBHOOK_URL: Optional[str] = None
    RSS_WEBHOOK_URL: Optional[str] = None
    BJP_WEBHOOK_URL: Optional[str] = None
    CONGRESS_WEBHOOK_URL: Optional[str] = None
    RELIGION_WEBHOOK_URL: Optional[str] = None
    ELECTION_WEBHOOK_URL: Optional[str] = None
    GEOPOLITICS_WEBHOOK_URL: Optional[str] = None
    
    class Config:
        env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env")

@lru_cache()
def get_settings():
    return Settings()
