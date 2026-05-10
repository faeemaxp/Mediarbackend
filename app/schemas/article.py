from pydantic import BaseModel, Field, HttpUrl
from datetime import datetime, timezone
from typing import List, Optional

class ArticleBase(BaseModel):
    title: str
    content: str
    source: str
    url: str
    published_at: datetime
    category_tags: List[str] = []
    topic_relevance: Optional[dict] = None
    people: List[str] = []
    organizations: List[str] = []
    keywords: List[str] = []
    sentiment: Optional[str] = None
    language: str = "en"
    summary: Optional[str] = None
    ai_intelligence: Optional[str] = None
    priority_score: int = 0
    is_saved: bool = False
    image_url: Optional[str] = None

class ArticleCreate(ArticleBase):
    pass

class ArticleUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    category_tags: Optional[List[str]] = None
    keywords: Optional[List[str]] = None
    sentiment: Optional[str] = None
    summary: Optional[str] = None

class ArticleInDB(ArticleBase):
    id: str = Field(alias="_id")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class ArticleResponse(ArticleBase):
    id: str
    created_at: datetime
