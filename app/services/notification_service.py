import aiohttp
import os
import logging
from backend.app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
_has_logged_missing_webhook = False

async def send_discord_alert(article: dict):
    global _has_logged_missing_webhook
    
    if not DISCORD_WEBHOOK_URL:
        if not _has_logged_missing_webhook:
            logger.warning("DISCORD_WEBHOOK_URL not set. Alerts will be skipped. Set this variable to enable realtime Discord notifications.")
            _has_logged_missing_webhook = True
        return

    payload = {
        "embeds": [{
            "title": f"🚨 HIGH PRIORITY: {article['title']}",
            "url": article['url'],
            "description": article['content'][:300] + "...",
            "color": 0xFF0000, # Red
            "fields": [
                {"name": "Source", "value": article['source'], "inline": True},
                {"name": "Priority Score", "value": str(article['priority_score']), "inline": True},
                {"name": "Categories", "value": ", ".join(article['category_tags']) or "None", "inline": False}
            ],
            "footer": {"text": "MediaRadar Intelligence Engine"}
        }]
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(DISCORD_WEBHOOK_URL, json=payload) as resp:
                if resp.status not in [200, 204]:
                    logger.error(f"Failed to send Discord alert: {resp.status}")
    except Exception as e:
        logger.error(f"Error sending Discord alert: {e}")
