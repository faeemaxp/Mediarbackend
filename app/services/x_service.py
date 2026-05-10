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

# List of Indian Right-Wing Media Handles on X
RIGHT_WING_HANDLES = [
    "OpIndia_com",
    "SwarajyaMag",
    "tfipost",
    "republic",
    "Republic_Bharat",
    "ZeeNews",
    "SudarshanNewsTV",
    "IndiaTVHindi",
    "sudhirchaudhary",
    "AMISHDEVGAN",
    "MrSinha_",
    "SushantBSinha",
    "Anand_Narasimhan",
    "TajinderBagga",
    "amitmalviya"
]

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
    normalized = "".join(e for e in title.lower() if e.isalnum())
    return hashlib.md5(normalized.encode()).hexdigest()

async def fetch_x_news(handles: List[str] = RIGHT_WING_HANDLES, limit: int = 10) -> List[Dict]:
    apify_token = os.getenv("APIFY_TOKEN")
    if not apify_token:
        logger.error("APIFY_TOKEN not found in environment variables")
        return []

    client = ApifyClient(apify_token)
    
    # Construct OR query for multiple handles
    # Example: from:FoxNews OR from:BreitbartNews
    query = " OR ".join([f"from:{handle}" for handle in handles])

    run_input = {
        "twitterContent": query,
        "maxItems": limit,
        "queryType": "Latest"
    }

    start_time = datetime.now(timezone.utc)
    try:
        logger.info(f"Starting Apify X scraper for handles: {handles}")
        # Using the cheapest pay-per-result actor that we verified works for free users
        run = client.actor("kaitoeasyapi/twitter-x-data-tweet-scraper-pay-per-result-cheapest").call(run_input=run_input)
        
        if run["status"] != "SUCCEEDED":
            logger.error(f"Apify X run failed with status: {run['status']}")
            await log_event("ERROR", f"X Scrape Failed", {"status": run["status"], "query": query})
            return []

        articles = []
        new_ingested = 0
        
        # Iterate over results
        for item in client.dataset(run["defaultDatasetId"]).iterate_items():
            url = item.get("url") or item.get("twitterUrl")
            text = item.get("text", "")
            
            if not url or not text:
                continue
                
            # Use first line or first 100 characters as title
            title = text.split('\n')[0][:100]
            if not title:
                title = f"Post by {item.get('author', {}).get('userName', 'unknown')}"
                
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
                # Parse date: "Sat May 09 22:53:57 +0000 2026"
                created_at_str = item.get("createdAt")
                if created_at_str:
                    try:
                        published_at = datetime.strptime(created_at_str, "%a %b %d %H:%M:%S %z %Y")
                    except ValueError:
                        published_at = datetime.now(timezone.utc)
                else:
                    published_at = datetime.now(timezone.utc)
                
                # Detect topics and score
                intelligence = detect_topics_and_score(title, text)
                is_intelligence = len(intelligence["topics"]) > 0 or intelligence["score"] >= 15
                
                # Extract image if available
                image_url = None
                media = item.get("extendedEntities", {}).get("media", [])
                if media and len(media) > 0:
                    image_url = media[0].get("media_url_https")

                author_name = item.get('author', {}).get('name', 'Unknown')
                author_handle = item.get('author', {}).get('userName', 'unknown')
                
                new_article = ArticleCreate(
                    title=title,
                    content=text,
                    source=f"X: {author_name} (@{author_handle})",
                    url=url,
                    published_at=published_at,
                    category_tags=intelligence["topics"],
                    topic_relevance=intelligence["topic_relevance"],
                    people=intelligence["people"],
                    organizations=intelligence["organizations"],
                    keywords=intelligence["topics"], # Fallback keywords
                    summary=text[:250],
                    language='en',
                    priority_score=intelligence["score"],
                    image_url=image_url
                )
                
                article_dict = new_article.model_dump()
                article_dict["is_intelligence"] = is_intelligence
                article_dict["title_hash"] = title_hash
                article_dict["created_at"] = datetime.now(timezone.utc)
                article_dict["metadata"] = {
                    "likes": item.get("likeCount"),
                    "retweets": item.get("retweetCount"),
                    "views": item.get("viewCount"),
                    "type": "x_post"
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
                logger.error(f"Error processing X post {url}: {e}")
                
        duration = (datetime.now(timezone.utc) - start_time).total_seconds()
        await log_event("INFO", f"Fetched X News", {"count": new_ingested, "duration": duration})
        return articles

    except Exception as e:
        logger.error(f"Failed to fetch X news: {e}")
        await log_event("ERROR", f"Failed to fetch X News", {"error": str(e)})
        return []
