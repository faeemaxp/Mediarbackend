from datetime import datetime, timedelta, timezone
from app.db.mongodb import db
from app.services.gemini_service import gemini_service
import logging

logger = logging.getLogger(__name__)

def get_time_of_day():
    hour = datetime.now().hour
    if 4 <= hour < 12:
        return "morning"
    elif 12 <= hour < 18:
        return "afternoon"
    else:
        return "night"

async def generate_and_save_briefing(force: bool = False):
    """
    Logic to generate a periodic intelligence briefing (Morning/Afternoon/Night).
    If not forced, checks for an existing briefing for the CURRENT window.
    """
    time_of_day = get_time_of_day()
    
    if not force:
        # Check if we already have a briefing for THIS edition today
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        latest_briefing = await db.db.briefings.find_one(
            {
                "created_at": {"$gte": today_start},
                "edition": time_of_day
            },
            sort=[("created_at", -1)]
        )
        if latest_briefing:
            return latest_briefing["content"], latest_briefing["created_at"], latest_briefing.get("edition")

    # Generate a new one
    # Try last 8 hours first for periodic updates, fallback to 24, 72
    top_articles = []
    for lookback_hours in [8, 24, 72]:
        since = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
        query = {
            "published_at": {"$gte": since},
            "priority_score": {"$gte": 30} # Slightly lower threshold to ensure we get "Top 5"
        }
        
        cursor = db.db.articles.find(query).sort("priority_score", -1).limit(20)
        top_articles = []
        async for doc in cursor:
            top_articles.append(doc)
            
        if len(top_articles) >= 5: # We want at least 5 for the "Top 5" requirement
            break
        
    if not top_articles:
        fallback_briefing = await db.db.briefings.find_one({}, sort=[("created_at", -1)])
        if fallback_briefing:
            return fallback_briefing["content"], fallback_briefing["created_at"], fallback_briefing.get("edition")
        return "No significant intelligence gathered in the last 72 hours to generate a briefing.", datetime.now(timezone.utc), None
        
    # Use the new periodic briefing generator
    briefing_content = await gemini_service.generate_periodic_briefing(top_articles, time_of_day)
    
    if briefing_content:
        new_briefing = {
            "content": briefing_content,
            "edition": time_of_day,
            "created_at": datetime.now(timezone.utc)
        }
        await db.db.briefings.insert_one(new_briefing)
        return briefing_content, new_briefing["created_at"], time_of_day
    else:
        fallback_briefing = await db.db.briefings.find_one({}, sort=[("created_at", -1)])
        if fallback_briefing:
            return fallback_briefing["content"], fallback_briefing["created_at"], fallback_briefing.get("edition")
        return "Intelligence synthesis is currently unavailable.", datetime.now(timezone.utc), None
