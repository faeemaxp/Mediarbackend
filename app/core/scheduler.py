from apscheduler.schedulers.asyncio import AsyncIOScheduler
from app.services.rss_service import fetch_rss_feed
from app.db.mongodb import db
import logging
import asyncio
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

async def fetch_all_feeds():
    logger.info("Starting scheduled RSS fetch for all active feeds concurrently")
    # Only fetch active sources that aren't consistently failing (fail_count < 5)
    cursor = db.db.sources.find({
        "active": True,
        "$or": [
            {"health.fail_count": {"$exists": False}},
            {"health.fail_count": {"$lt": 5}}
        ]
    })
    sources = await cursor.to_list(1000)
    
    async def fetch_single(source):
        if not source.get("rss_url"):
            return
        try:
            articles = await fetch_rss_feed(source["rss_url"], source["name"])
            logger.info(f"Fetched {len(articles)} articles from {source['name']}")
        except Exception as e:
            logger.error(f"Error fetching {source['name']}: {e}")

    await asyncio.gather(*(fetch_single(s) for s in sources))





async def generate_periodic_briefing_job():
    logger.info("Generating scheduled periodic briefing")
    try:
        from app.services.briefing_service import generate_and_save_briefing
        from app.services.notification_service import send_briefing_notification
        
        content, timestamp, edition = await generate_and_save_briefing(force=True)
        if edition:
            await send_briefing_notification(content, edition)
    except Exception as e:
        logger.error(f"Error in scheduled briefing job: {e}")


async def digest_job():
    """
    Periodic digest sender — runs every 45 minutes.
    """
    logger.info("Starting periodic channel digest job")
    try:
        from app.services.notification_service import send_all_digests
        await send_all_digests()
    except Exception as e:
        logger.error(f"Error in scheduled digest job: {e}")

async def weekly_report_job_trigger():
    """Weekly Excel export job."""
    try:
        from app.services.report_service import weekly_report_job
        await weekly_report_job()
    except Exception as e:
        logger.error(f"Error in scheduled weekly report job: {e}")


async def cleanup_old_articles_job():
    """Weekly cleanup: delete non-saved articles older than 7 days."""
    try:
        from app.db.mongodb import db
        from datetime import datetime, timezone, timedelta
        import logging
        logger = logging.getLogger(__name__)
        
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        result = await db.db.articles.delete_many({
            "published_at": {"$lt": cutoff},
            "is_saved": {"$ne": True}   # always keep bookmarked articles
        })
        logger.info(f"[Cleanup] Deleted {result.deleted_count} articles older than 7 days")
        return result.deleted_count
    except Exception as e:
        logger.error(f"Error in cleanup old articles job: {e}")
        return 0


# ---------------------------------------------------------------------------
# Mock News Spawner Job (Runs on startup and every 3 minutes)
# ---------------------------------------------------------------------------
import random
import string
import hashlib

def generate_mock_article(category: str):
    templates = {
        "BJP": (
            "🪷 BHARATIYA JANATA PARTY: High-Level National Strategy Meet Convened",
            "A closed-door strategic meeting of senior leadership was convened today to discuss upcoming legislative focus and organizational updates."
        ),
        "Congress": (
            "✋ CONGRESS: Opposition Alliance Finalizes Joint Policy Platform",
            "Opposition party leaders met to align on core policy stances and outline their legislative coordination strategy for the upcoming session."
        ),
        "RSS": (
            "🕉️ RSS: Annual Coordination Conclave Commences with National Focus",
            "The annual coordination conclave commenced today, focusing on social outreach initiatives and social welfare projects nationwide."
        ),
        "Religion": (
            "⛪ RELIGION: Archaeological Heritage Bill Tabled for Ancient Sites",
            "A new legislative draft has been introduced to strengthen security and conservation frameworks for key historic and cultural sites."
        ),
        "Election": (
            "🗳️ ELECTION: Election Commission Outlines Digital Security Protocols",
            "The Election Commission announced updated protocols for digital voting validation and booth security to ensure maximum transparency."
        ),
        "Geopolitics": (
            "🌏 GEOPOLITICS: Bilateral Maritime Safety Treaty Signed in Region",
            "Bilateral maritime safety agreements were finalized today to ensure trade route protection and coordinate joint patrol efforts."
        ),
        "Politics": (
            "🏛️ POLITICS: Administrative Reforms Package Introduced in Assembly",
            "A sweeping set of administrative reform proposals aimed at improving public service delivery was tabled in the assembly today."
        ),
        "Tamil": (
            "🎌 TAMIL: Cultural Preservation Grant Program Announced for Arts",
            "A major state-backed grant program was unveiled today to support local artisans and preserve classical language archives."
        )
    }
    
    title, content = templates.get(category, (
        "📡 GENERAL: System Event Log Ingest Validation Alert",
        "A system verification alert has been generated by the server to validate database notification routing rules."
    ))
    
    # Add a random string to title to make it unique and bypass deduplication
    rand_suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
    full_title = f"{title} [{rand_suffix}]"
    
    normalized = "".join(e for e in full_title.lower() if e.isalnum())
    title_hash = hashlib.md5(normalized.encode()).hexdigest()
    
    return {
        "title": full_title,
        "content": content,
        "source": "System Mock Ingest",
        "url": f"http://127.0.0.1:8000/mock-news/{title_hash}",
        "published_at": datetime.now(timezone.utc),
        "category_tags": [category],
        "topic_relevance": {category: 90},
        "priority_score": random.randint(85, 99),
        "title_hash": title_hash,
        "is_saved": False,
        "is_intelligence": True,
        "created_at": datetime.now(timezone.utc)
    }

async def mock_news_spawner_job():
    logger.info("Spawning 7 random high-priority mock news articles across categories")
    categories = ["BJP", "Congress", "RSS", "Religion", "Election", "Geopolitics", "Politics", "Tamil"]
    
    # Pick 7 random categories
    chosen_categories = [random.choice(categories) for _ in range(7)]
    
    from app.services.notification_service import send_discord_alert
    
    for cat in chosen_categories:
        article = generate_mock_article(cat)
        # Save to database
        await db.db.articles.insert_one(article)
        # Push notification (bypassing the queue stagger)
        await send_discord_alert(article, bypass_stagger=True)
        # Short stagger to prevent Discord webhook rate-limit 429
        await asyncio.sleep(0.5)


# ---------------------------------------------------------------------------
# Scheduler setup
# ---------------------------------------------------------------------------

def setup_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()

    # --- Data ingestion ---
    # RSS feeds every 30 minutes, starting immediately on startup
    scheduler.add_job(fetch_all_feeds, "interval", minutes=30,
                      id="rss_fetch", name="RSS Feed Fetch",
                      next_run_time=datetime.now(timezone.utc))

    # --- Mock News Spawner (Every 3 minutes, starting immediately on startup) ---
    scheduler.add_job(mock_news_spawner_job, "interval", minutes=3,
                      id="mock_spawner", name="Mock News Spawner",
                      next_run_time=datetime.now(timezone.utc))



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

    # --- Weekly Excel Report (Every Monday at 9:00 AM UTC) ---
    scheduler.add_job(weekly_report_job_trigger, "cron",
                      day_of_week="mon", hour=9, minute=0,
                      id="weekly_report", name="Weekly Excel Export")

    # --- Weekly DB Cleanup (Every Sunday at 00:00 UTC) ---
    scheduler.add_job(cleanup_old_articles_job, "cron",
                      day_of_week="sun", hour=0, minute=0,
                      id="weekly_cleanup", name="Weekly Article Cleanup")

    scheduler.start()
    logger.info("Scheduler started with jobs: RSS(30m), Briefings(3×/day), Digests(45m)")
    return scheduler
