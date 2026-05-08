from fastapi import APIRouter, HTTPException, Header, Depends
from typing import List, Optional
from backend.app.schemas.source import SourceCreate, SourceUpdate, SourceResponse
from backend.app.schemas.article import ArticleCreate
from backend.app.db.mongodb import db
from backend.app.services.rss_service import fetch_rss_feed
from backend.app.services.topic_service import detect_topics_and_score
from bson import ObjectId
from datetime import datetime, timezone
import feedparser
import time
import os

from backend.app.core.config import get_settings

router = APIRouter()
settings = get_settings()

async def verify_admin(x_admin_token: str = Header(...)):
    if x_admin_token != settings.ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid admin token")

@router.get("/sources", response_model=List[SourceResponse], dependencies=[Depends(verify_admin)])
async def get_admin_sources():
    cursor = db.db.sources.find()
    sources = []
    async for doc in cursor:
        doc["id"] = str(doc.pop("_id"))
        if "health" not in doc:
            doc["health"] = {"status": "unknown"}
        sources.append(doc)
    return sources

@router.post("/test-source", dependencies=[Depends(verify_admin)])
async def test_source(rss_url: str):
    start_time = time.time()
    try:
        feed = feedparser.parse(rss_url)
        duration = time.time() - start_time
        
        if feed.bozo:
            return {
                "valid": False,
                "error": str(feed.bozo_exception),
                "duration": duration
            }
        
        sample = None
        if len(feed.entries) > 0:
            entry = feed.entries[0]
            sample = {
                "title": entry.title,
                "link": entry.link,
                "published": entry.get("published", "N/A")
            }
            
        return {
            "valid": True,
            "article_count": len(feed.entries),
            "sample_article": sample,
            "duration": duration,
            "title": feed.feed.get("title", "Unknown Feed")
        }
    except Exception as e:
        return {
            "valid": False,
            "error": str(e),
            "duration": time.time() - start_time
        }

@router.post("/force-scrape", dependencies=[Depends(verify_admin)])
async def force_scrape(source_id: Optional[str] = None):
    if source_id:
        if not ObjectId.is_valid(source_id):
            raise HTTPException(status_code=400, detail="Invalid ID")
        source = await db.db.sources.find_one({"_id": ObjectId(source_id)})
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")
        
        count = await fetch_rss_feed(source["rss_url"], source["name"])
        return {"message": f"Fetched {len(count)} articles from {source['name']}"}
    else:
        # Force all
        from backend.app.core.scheduler import fetch_all_feeds
        await fetch_all_feeds()
        return {"message": "All sources triggered for scraping"}

@router.post("/push-article", dependencies=[Depends(verify_admin)])
async def push_test_article(article: ArticleCreate):
    article_dict = article.model_dump()
    article_dict["created_at"] = datetime.now(timezone.utc)
    article_dict["priority_score"] = article_dict.get("priority_score", 0)
    
    # Generate a fake hash if missing
    import hashlib
    normalized = "".join(e for e in article.title.lower() if e.isalnum())
    article_dict["title_hash"] = hashlib.md5(normalized.encode()).hexdigest()
    
    result = await db.db.articles.insert_one(article_dict)
    
    # Trigger alert if score is high
    if article_dict["priority_score"] >= 50:
        from backend.app.services.notification_service import send_discord_alert
        await send_discord_alert(article_dict)
        
    return {"message": "Article injected", "id": str(result.inserted_id)}

@router.delete("/sources/{source_id}", dependencies=[Depends(verify_admin)])
async def delete_source(source_id: str):
    if not ObjectId.is_valid(source_id):
        raise HTTPException(status_code=400, detail="Invalid ID")
    result = await db.db.sources.delete_one({"_id": ObjectId(source_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Source not found")
    return {"message": "Source deleted"}

@router.patch("/sources/{source_id}", response_model=SourceResponse, dependencies=[Depends(verify_admin)])
async def update_source(source_id: str, source_update: SourceUpdate):
    if not ObjectId.is_valid(source_id):
        raise HTTPException(status_code=400, detail="Invalid ID")
        
    update_data = {k: v for k, v in source_update.model_dump().items() if v is not None}
    
    result = await db.db.sources.find_one_and_update(
        {"_id": ObjectId(source_id)},
        {"$set": update_data},
        return_document=True
    )
    
    if not result:
        raise HTTPException(status_code=404, detail="Source not found")
        
    result["id"] = str(result.pop("_id"))
    return result
