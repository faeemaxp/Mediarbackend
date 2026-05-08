from apscheduler.schedulers.asyncio import AsyncIOScheduler
from backend.app.services.rss_service import fetch_rss_feed
from backend.app.db.mongodb import db
import logging

logger = logging.getLogger(__name__)

async def fetch_all_feeds():
    logger.info("Starting scheduled RSS fetch")
    cursor = db.db.sources.find({"active": True})
    async for source in cursor:
        if source.get("rss_url"):
            try:
                articles = await fetch_rss_feed(source["rss_url"], source["name"])
                logger.info(f"Fetched {len(articles)} articles from {source['name']}")
            except Exception as e:
                logger.error(f"Error fetching {source['name']}: {e}")

def setup_scheduler():
    scheduler = AsyncIOScheduler()
    # Fetch feeds every 30 minutes
    scheduler.add_job(fetch_all_feeds, 'interval', minutes=30)
    scheduler.start()
    return scheduler
