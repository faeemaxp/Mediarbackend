from pydantic import BaseModel, HttpUrl, Field
from typing import List, Optional
from datetime import datetime

class SourceHealth(BaseModel):
    last_fetch: Optional[datetime] = None
    status: str = "unknown" # active, failing, inactive
    fail_count: int = 0
    avg_response_time: float = 0.0
    last_error: Optional[str] = None
    total_articles_ingested: int = 0

class SourceBase(BaseModel):
    name: str
    url: Optional[str] = None
    rss_url: Optional[str] = None
    category: str = "general"
    categories: List[str] = [] # Multiple tags support
    active: bool = True
    priority: int = Field(default=1, ge=1, le=5)
    source_type: str = "rss"

class SourceCreate(SourceBase):
    pass

class SourceUpdate(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    rss_url: Optional[str] = None
    category: Optional[str] = None
    categories: Optional[List[str]] = None
    active: Optional[bool] = None
    priority: Optional[int] = None
    source_type: Optional[str] = None

class SourceResponse(SourceBase):
    id: str
    created_at: datetime
    health: SourceHealth = Field(default_factory=SourceHealth)
