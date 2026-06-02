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
    logger.warning("fetch_x_news was called, but X/Twitter scraping features have been disabled.")
    return []
