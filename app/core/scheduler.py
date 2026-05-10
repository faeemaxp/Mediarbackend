from apscheduler.schedulers.asyncio import AsyncIOScheduler
from app.services.rss_service import fetch_rss_feed
from app.services.x_service import fetch_x_news
from app.db.mongodb import db
import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

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


async def fetch_x_job():
    logger.info("Starting scheduled X fetch")
    articles = await fetch_x_news(limit=10)
    logger.info(f"Fetched {len(articles)} articles from X")


async def generate_periodic_briefing_job():
    logger.info("Generating scheduled periodic briefing")
    from app.services.briefing_service import generate_and_save_briefing
    await generate_and_save_briefing(force=True)


async def digest_job():
    """
    Periodic digest sender — runs every 45 minutes.
    Each Discord channel receives a randomly-styled notification
    (Breaking / Digest / Roundup) based on what articles exist.
    Per-channel cooldowns prevent spam even if this job fires frequently.
    """
    logger.info("Starting periodic channel digest job")
    from app.services.notification_service import send_all_digests
    await send_all_digests()


# ---------------------------------------------------------------------------
# Scheduler setup
# ---------------------------------------------------------------------------

def setup_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()

    # --- Data ingestion ---
    # RSS feeds every 15 minutes
    scheduler.add_job(fetch_all_feeds, "interval", minutes=15,
                      id="rss_fetch", name="RSS Feed Fetch")

    # X / Twitter every hour
    scheduler.add_job(fetch_x_job, "interval", hours=1,
                      id="x_fetch", name="X Feed Fetch")

    # --- AI Briefings (3× per day, UTC) ---
    scheduler.add_job(generate_periodic_briefing_job, "cron",
                      hour=8, minute=0, id="briefing_morning",  name="Morning Briefing")
    scheduler.add_job(generate_periodic_briefing_job, "cron",
                      hour=14, minute=0, id="briefing_afternoon", name="Afternoon Briefing")
    scheduler.add_job(generate_periodic_briefing_job, "cron",
                      hour=21, minute=0, id="briefing_night",   name="Night Briefing")

    # --- Discord channel digests every 45 minutes ---
    # Each channel independently checks its own 30-min cooldown, so they
    # will naturally stagger and never all fire at exactly the same time.
    scheduler.add_job(digest_job, "interval", minutes=45,
                      id="digest_job", name="Discord Channel Digests")

    scheduler.start()
    logger.info("Scheduler started with jobs: RSS(15m), X(1h), Briefings(3×/day), Digests(45m)")
    return scheduler
