import os
from datetime import datetime, timezone
import hashlib
from typing import List, Dict
from apify_client import ApifyClient
from app.schemas.article import ArticleCreate
from app.db.mongodb import db
from app.services.topic_service import detect_topics_and_score
from app.services.notification_service import send_discord_alert
import logging

logger = logging.getLogger(__name__)

async def log_event(level: str, message: str, details: Dict = None):
    try:
        await db.db.logs.insert_one({
            "timestamp": datetime.now(timezone.utc),
            "level": level,
            "message": message,
            "details": details or {}
        })
    except Exception as e:
        logger.error(f"Failed to log event: {e}")

def generate_title_hash(title: str) -> str:
    # Normalize title: lowercase, remove non-alphanumeric
    normalized = "".join(e for e in title.lower() if e.isalnum())
    return hashlib.md5(normalized.encode()).hexdigest()

async def fetch_instagram_news(query: str = "#news", limit: int = 10) -> List[Dict]:
    apify_token = os.getenv("APIFY_TOKEN")
    if not apify_token:
        logger.error("APIFY_TOKEN not found in environment variables")
        return []

    client = ApifyClient(apify_token)
    
    # Construct direct URL if it's a hashtag or account name
    if query.startswith("#"):
        hashtag = query[1:]
        direct_urls = [f"https://www.instagram.com/explore/tags/{hashtag}/"]
    elif query.startswith("http"):
        direct_urls = [query]
    else:
        # Assume it's a username
        direct_urls = [f"https://www.instagram.com/{query}/"]

    run_input = {
        "directUrls": direct_urls,
        "resultsLimit": limit,
    }

    start_time = datetime.now(timezone.utc)
    try:
        logger.info(f"Starting Apify Instagram scraper for: {direct_urls}")
        run = client.actor("apify/instagram-scraper").call(run_input=run_input)
        
        if run["status"] != "SUCCEEDED":
            logger.error(f"Apify run failed with status: {run['status']}")
            await log_event("ERROR", f"Instagram Scrape Failed: {query}", {"status": run["status"]})
            return []

        articles = []
        new_ingested = 0
        
        # Iterate over results
        for item in client.dataset(run["defaultDatasetId"]).iterate_items():
            url = item.get("url")
            caption = item.get("caption", "")
            
            if not url or not caption:
                continue
                
            # Use first 100 characters of caption as title if no better title exists
            title = caption.split('\n')[0][:100]
            title_hash = generate_title_hash(title)
            
            # Deduplicate
            existing = await db.db.articles.find_one({
                "$or": [
                    {"url": url},
                    {"title_hash": title_hash}
                ]
            })
            
            if existing:
                continue
                
            try:
                published_at_str = item.get("timestamp")
                if published_at_str:
                    published_at = datetime.fromisoformat(published_at_str.replace("Z", "+00:00"))
                else:
                    published_at = datetime.now(timezone.utc)
                
                # Detect topics and score
                intelligence = detect_topics_and_score(title, caption)
                is_intelligence = len(intelligence["topics"]) > 0 or intelligence["score"] >= 15
                
                new_article = ArticleCreate(
                    title=title,
                    content=caption,
                    source=f"Instagram: {item.get('ownerUsername', 'unknown')}",
                    url=url,
                    published_at=published_at,
                    category_tags=intelligence["topics"],
                    topic_relevance=intelligence["topic_relevance"],
                    people=intelligence["people"],
                    organizations=intelligence["organizations"],
                    keywords=item.get("hashtags", []),
                    summary=caption[:250],
                    language='en',
                    priority_score=intelligence["score"],
                    image_url=item.get("displayUrl")
                )
                
                article_dict = new_article.model_dump()
                article_dict["is_intelligence"] = is_intelligence
                article_dict["title_hash"] = title_hash
                article_dict["created_at"] = datetime.now(timezone.utc)
                article_dict["metadata"] = {
                    "likes": item.get("likesCount"),
                    "comments": item.get("commentsCount"),
                    "type": "instagram_post"
                }
                
                await db.db.articles.insert_one(article_dict)
                articles.append(article_dict)
                
                # Elite Notification Trigger: Only alert for high priority (default 80+)
                from app.core.config import get_settings
                settings = get_settings()
                if intelligence["score"] >= settings.MIN_PRIORITY_SCORE:
                    await send_discord_alert(article_dict)

                
                new_ingested += 1
            except Exception as e:
                logger.error(f"Error processing Instagram post {url}: {e}")
                
        duration = (datetime.now(timezone.utc) - start_time).total_seconds()
        await log_event("INFO", f"Fetched Instagram News: {query}", {"count": new_ingested, "duration": duration})
        return articles

    except Exception as e:
        logger.error(f"Failed to fetch Instagram news: {e}")
        await log_event("ERROR", f"Failed to fetch Instagram News: {query}", {"error": str(e)})
        return []
