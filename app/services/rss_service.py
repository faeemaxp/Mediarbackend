import feedparser
from newspaper import Article as NewspaperArticle
from datetime import datetime, timezone
import time
import hashlib
from typing import List, Dict
from backend.app.schemas.article import ArticleCreate
from backend.app.db.mongodb import db
from backend.app.services.topic_service import detect_topics_and_score
from backend.app.services.notification_service import send_discord_alert
from backend.app.services.gemini_service import gemini_service
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

def clean_url(url: str) -> str:
    """Handles cases where absolute URLs are incorrectly prepended with a base URL."""
    if not url:
        return url
    
    # Check for double http/https (common in malformed RSS feeds)
    # Example: https://base.com/https://site.com/path
    parts = url.split("http")
    if len(parts) > 2:
        # Take the last complete http URL
        cleaned = "http" + parts[-1]
        # remove trailing slashes and common artifacts
        return cleaned.strip().rstrip("/")
    return url.strip().rstrip("/")

async def fetch_rss_feed(feed_url: str, source_name: str) -> List[Dict]:
    start_time = time.time()
    try:
        feed = feedparser.parse(feed_url)
        duration = time.time() - start_time
        await log_event("INFO", f"Fetched {source_name}", {"url": feed_url, "duration": duration, "articles": len(feed.entries)})
    except Exception as e:
        logger.error(f"Failed to parse feed {feed_url}: {e}")
        await log_event("ERROR", f"Failed to parse {source_name}", {"url": feed_url, "error": str(e)})
        await db.db.sources.update_one(
            {"name": source_name},
            {"$set": {"health.status": "failing", "health.last_error": str(e)}, "$inc": {"health.fail_count": 1}}
        )
        return []

    articles = []
    new_ingested = 0
    
    for entry in feed.entries:
        link = clean_url(entry.link)
        title_hash = generate_title_hash(entry.title)
        
        # Deduplicate by URL or Title Hash
        existing = await db.db.articles.find_one({
            "$or": [
                {"url": link},
                {"title_hash": title_hash}
            ]
        })
        
        if existing:
            continue
            
        try:
            # Robust date parsing
            published_at = datetime.now(timezone.utc)
            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                try:
                    published_at = datetime.fromtimestamp(time.mktime(entry.published_parsed), tz=timezone.utc)
                except (ValueError, TypeError, OverflowError):
                    pass
            
            # Use newspaper3k to get full content if possible
            content = ""
            keywords = []
            summary = ""
            image_url = None
            
            # Check RSS enclosures for image fallback
            if hasattr(entry, 'enclosures') and len(entry.enclosures) > 0:
                for enc in entry.enclosures:
                    if 'type' in enc and enc.type.startswith('image/'):
                        image_url = enc.href
                        break
            
            # Skip extraction for obvious non-HTML content
            if not link.lower().endswith(('.pdf', '.jpg', '.png', '.jpeg', '.gif', '.zip')):
                try:
                    # Added configuration to NewspaperArticle to be less aggressive
                    from newspaper import Config
                    config = Config()
                    config.browser_user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                    config.request_timeout = 10
                    
                    article_data = NewspaperArticle(link, config=config)
                    article_data.download()
                    article_data.parse()
                    
                    # NLP for keywords and summary
                    try:
                        import nltk
                        try:
                            nltk.data.find('tokenizers/punkt_tab')
                        except (LookupError, AttributeError):
                            nltk.download('punkt', quiet=True)
                            nltk.download('punkt_tab', quiet=True)
                        
                        article_data.nlp()
                        content = article_data.text
                        if hasattr(article_data, 'keywords'):
                            keywords = article_data.keywords
                        if hasattr(article_data, 'summary'):
                            summary = article_data.summary
                    except Exception as nlp_ex:
                        logger.warning(f"NLP extraction failed for {link}: {nlp_ex}")
                        content = article_data.text
                        
                    if hasattr(article_data, 'top_image') and article_data.top_image:
                        image_url = article_data.top_image
                except Exception as ex:
                    # Downgraded to warning to avoid polluting logs with every 404
                    logger.warning(f"Full text extraction skipped for {link}: {ex}")
            
            if not content:
                # Fallback to summary
                content = entry.get('summary', entry.get('description', ''))
            
            # Use newspaper3k summary if available and content fallback was used
            if not summary:
                summary = entry.get('summary', entry.get('description', ''))[:250]
                
            # If content is still somehow empty or just whitespace, skip it
            if not content or not content.strip():
                 continue
                 
            # Detect topics and priority score
            intelligence = detect_topics_and_score(entry.title, content)
            
            # Use boolean flag to distinguish intelligence vs general news
            is_intelligence = len(intelligence["topics"]) > 0 or intelligence["score"] >= 15
                 
            new_article = ArticleCreate(
                title=entry.title,
                content=content,
                source=source_name,
                url=link,
                published_at=published_at,
                category_tags=intelligence["topics"],
                topic_relevance=intelligence["topic_relevance"],
                people=intelligence["people"],
                organizations=intelligence["organizations"],
                keywords=keywords,
                summary=summary,
                language='en',
                priority_score=intelligence["score"],
                image_url=image_url
            )

            
            # Save to DB
            article_dict = new_article.model_dump()
            article_dict["is_intelligence"] = is_intelligence # Added for prioritized sorting
            article_dict["title_hash"] = title_hash
            article_dict["created_at"] = datetime.now(timezone.utc)
            
            await db.db.articles.insert_one(article_dict)
            articles.append(article_dict)
            
            # Elite Notification Trigger: Only alert for extremely high priority (80+)
            if intelligence["score"] >= 80:
                await send_discord_alert(article_dict)
            
            new_ingested += 1
            
        except Exception as e:
            logger.error(f"Error processing article {entry.link}: {e}")
            
    # Update health metrics
    await db.db.sources.update_one(
        {"name": source_name},
        {
            "$set": {
                "health.last_fetch": datetime.now(timezone.utc),
                "health.status": "active",
                "health.avg_response_time": duration,
                "health.last_error": None
            },
            "$inc": {"health.total_articles_ingested": new_ingested}
        }
    )
            
    return articles
