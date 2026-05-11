from fastapi import APIRouter, HTTPException, Header, Depends, BackgroundTasks
from typing import List, Optional
from app.schemas.source import SourceCreate, SourceUpdate, SourceResponse
from app.schemas.article import ArticleCreate
from app.db.mongodb import db
from app.services.rss_service import fetch_rss_feed
from app.services.topic_service import detect_topics_and_score
from bson import ObjectId
from datetime import datetime, timezone, timedelta
import feedparser
import asyncio
import time
import os

from app.core.config import get_settings

router = APIRouter()
settings = get_settings()

async def verify_admin(x_admin_token: str = Header(...)):
    if x_admin_token != settings.ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid admin token")

@router.get("/sources", response_model=List[SourceResponse], dependencies=[Depends(verify_admin)])
async def get_admin_sources():
    cursor = db.db.sources.find().sort("order", 1)
    sources = []
    async for doc in cursor:
        doc["id"] = str(doc.pop("_id"))
        if "health" not in doc:
            doc["health"] = {"status": "unknown"}
        sources.append(doc)
    return sources

@router.post("/sources/", response_model=SourceResponse, dependencies=[Depends(verify_admin)])
async def create_admin_source(source: SourceCreate):
    source_dict = source.model_dump()
    source_dict["created_at"] = datetime.now(timezone.utc)
    # Initialize health
    source_dict["health"] = {
        "status": "active",
        "fail_count": 0,
        "avg_response_time": 0.0,
        "total_articles_ingested": 0
    }
    result = await db.db.sources.insert_one(source_dict)
    source_dict["id"] = str(result.inserted_id)
    return source_dict

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
        from app.core.scheduler import fetch_all_feeds
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
        from app.services.notification_service import send_discord_alert
        await send_discord_alert(article_dict)
        
    return {"message": "Article injected", "id": str(result.inserted_id)}

@router.post("/trigger-briefing", dependencies=[Depends(verify_admin)])
async def trigger_briefing(background_tasks: BackgroundTasks):
    from app.services.briefing_service import generate_and_save_briefing
    from app.services.notification_service import send_briefing_notification
    
    content, created_at, edition = await generate_and_save_briefing(force=True)
    
    async def _post():
        if edition:
            await send_briefing_notification(content, edition)
    background_tasks.add_task(_post)
    
    return {"message": "Briefing triggered and queued for #Briefings", "edition": edition}

@router.post("/retag-all", dependencies=[Depends(verify_admin)])
async def trigger_retag_all():
    from app.services.topic_service import detect_topics_and_score
    
    cursor = db.db.articles.find({})
    updated = 0
    
    async for article in cursor:
        intelligence = detect_topics_and_score(article["title"], article["content"])
        
        # Use boolean flag to distinguish intelligence vs general news
        is_intelligence = len(intelligence["topics"]) > 0 or intelligence.get("score", 0) >= 15
            
        await db.db.articles.update_one(
            {"_id": article["_id"]},
            {"$set": {
                "category_tags": intelligence["topics"],
                "topic_relevance": intelligence.get("topic_relevance", {}),
                "priority_score": intelligence["score"],
                "people": intelligence["people"],
                "organizations": intelligence["organizations"],
                "is_intelligence": is_intelligence
            }}
        )
        updated += 1
        
    return {"message": "Retagging complete", "updated": updated}

@router.post("/sources/{source_id}/move", dependencies=[Depends(verify_admin)])
async def move_source(source_id: str, direction: str):
    if not ObjectId.is_valid(source_id):
        raise HTTPException(status_code=400, detail="Invalid ID")
    
    # Get all sources to determine current order
    sources = await db.db.sources.find().to_list(1000)
    
    idx = -1
    for i, s in enumerate(sources):
        if str(s["_id"]) == source_id:
            idx = i
            break
    
    if idx == -1:
        raise HTTPException(status_code=404, detail="Source not found")
    
    if direction == "up" and idx > 0:
        # Swap with previous
        sources[idx], sources[idx-1] = sources[idx-1], sources[idx]
    elif direction == "down" and idx < len(sources) - 1:
        # Swap with next
        sources[idx], sources[idx+1] = sources[idx+1], sources[idx]
    else:
        return {"message": "Already at boundary"}

    # In a real system we'd use a dedicated 'order' field, 
    # but since we are relying on retrieval order, we can't easily swap without a field.
    # Let's add an 'order' field if it doesn't exist.
    for i, s in enumerate(sources):
        await db.db.sources.update_one({"_id": s["_id"]}, {"$set": {"order": i}})
        
    return {"message": "Source order updated"}

@router.delete("/sources/{source_id}", dependencies=[Depends(verify_admin)])
async def delete_source(source_id: str):
    if not ObjectId.is_valid(source_id):
        raise HTTPException(status_code=400, detail="Invalid ID")
    result = await db.db.sources.delete_one({"_id": ObjectId(source_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Source not found")
    return {"message": "Source deleted"}

@router.get("/logs", dependencies=[Depends(verify_admin)])
async def get_system_logs(limit: int = 50):
    cursor = db.db.logs.find().sort("timestamp", -1).limit(limit)
    logs = []
    async for doc in cursor:
        doc["id"] = str(doc.pop("_id"))
        logs.append(doc)
    return logs

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


@router.get("/stats", dependencies=[Depends(verify_admin)])
async def get_system_stats():
    """Comprehensive system stats for the admin dashboard."""
    now = datetime.now(timezone.utc)
    last_24h = now - timedelta(hours=24)
    last_1h  = now - timedelta(hours=1)

    # Article counts
    total_articles   = await db.db.articles.count_documents({})
    articles_24h     = await db.db.articles.count_documents({"published_at": {"$gte": last_24h}})
    articles_1h      = await db.db.articles.count_documents({"published_at": {"$gte": last_1h}})
    saved_articles   = await db.db.articles.count_documents({"is_saved": True})
    high_priority    = await db.db.articles.count_documents({"priority_score": {"$gte": 75}})

    # Source health
    total_sources  = await db.db.sources.count_documents({})
    active_sources = await db.db.sources.count_documents({"active": True})
    failing_sources= await db.db.sources.count_documents({"health.status": "failing"})

    # Per-pipeline breakdown
    TAGS = ["BJP", "Congress", "RSS", "Religion", "Election", "Geopolitics", "Politics", "Tamil"]
    pipeline_counts = {}
    for tag in TAGS:
        pipeline_counts[tag] = await db.db.articles.count_documents({"category_tags": tag})

    # Latest briefing
    latest_briefing = await db.db.briefings.find_one(sort=[("created_at", -1)])
    briefing_info = None
    if latest_briefing:
        briefing_info = {
            "edition": latest_briefing.get("edition", "unknown"),
            "created_at": latest_briefing.get("created_at"),
            "preview": (latest_briefing.get("content", "") or "")[:200]
        }

    # Notification queue depth
    from app.services.notification_service import notification_queue, hourly_counter, HOURLY_CAP
    queue_depth = notification_queue.qsize()

    # Digest state per channel
    digest_states = []
    async for row in db.db.digest_state.find():
        digest_states.append({
            "tag": row.get("tag"),
            "last_style": row.get("last_style"),
            "last_digest": row.get("last_digest"),
        })

    return {
        "articles": {
            "total": total_articles,
            "last_24h": articles_24h,
            "last_1h": articles_1h,
            "saved": saved_articles,
            "high_priority": high_priority,
        },
        "sources": {
            "total": total_sources,
            "active": active_sources,
            "failing": failing_sources,
        },
        "pipelines": pipeline_counts,
        "notifications": {
            "queue_depth": queue_depth,
            "hourly_sent": hourly_counter,
            "hourly_cap": HOURLY_CAP,
        },
        "briefing": briefing_info,
        "digest_states": digest_states,
        "generated_at": now,
    }


@router.post("/test-digest", dependencies=[Depends(verify_admin)])
async def test_digest(tag: str = "General", background_tasks: BackgroundTasks = None):
    """Manually trigger a digest for a specific channel tag (for testing)."""
    from app.services.notification_service import send_channel_digest, get_webhook_for_tag
    webhook_url = await get_webhook_for_tag(tag)
    if not webhook_url:
        raise HTTPException(status_code=404, detail=f"No webhook configured for #{tag}")
    # Run in background so the API returns immediately
    async def _run():
        await send_channel_digest(tag, webhook_url)
    asyncio.create_task(_run())
    return {"message": f"Digest triggered for #{tag}", "webhook_found": True}


@router.post("/export-excel", dependencies=[Depends(verify_admin)])
async def trigger_excel_export(
    days: int = 7, 
    min_score: int = 0,
    tags: Optional[str] = None,
    dest_tag: str = "General", 
    background_tasks: BackgroundTasks = None
):
    """Manually trigger a customized Excel export and send to Discord."""
    from app.services.report_service import generate_article_report, send_report_to_discord
    
    tag_list = [t.strip() for t in tags.split(",")] if tags else None
    
    async def _process():
        filepath = await generate_article_report(days=days, min_score=min_score, tags=tag_list)
        if filepath:
            await send_report_to_discord(filepath, channel_tag=dest_tag)
            
    background_tasks.add_task(_process)
    return {"message": f"Custom Excel export started. Filtering: {days}d, Min Score: {min_score}, Tags: {tags or 'All'}"}
