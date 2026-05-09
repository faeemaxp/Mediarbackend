import aiohttp
import os
import logging
from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

def get_webhook_for_topic(topic: str) -> str:
    topic_map = {
        "RSS": settings.RSS_WEBHOOK_URL,
        "BJP": settings.BJP_WEBHOOK_URL,
        "Congress": settings.CONGRESS_WEBHOOK_URL,
        "Religion": settings.RELIGION_WEBHOOK_URL,
        "Election": settings.ELECTION_WEBHOOK_URL,
        "Geopolitics": settings.GEOPOLITICS_WEBHOOK_URL
    }
    return topic_map.get(topic) or settings.DISCORD_WEBHOOK_URL

async def send_discord_alert(article: dict):
    # Determine which webhooks to send to
    webhooks = set()
    
    # Check specific topics
    for topic in article.get('category_tags', []):
        webhook = get_webhook_for_topic(topic)
        if webhook:
            webhooks.add(webhook)
            
    # If no topic-specific webhooks found, use default
    if not webhooks and settings.DISCORD_WEBHOOK_URL:
        webhooks.add(settings.DISCORD_WEBHOOK_URL)
        
    if not webhooks:
        return

    # Prepare payload
    people_str = ", ".join(article.get('people', [])) or "None"
    orgs_str = ", ".join(article.get('organizations', [])) or "None"
    
    payload = {
        "embeds": [{
            "title": f"🚨 INTELLIGENCE ALERT: {article['title']}",
            "url": article['url'],
            "description": article.get('summary') or (article['content'][:400] + "..."),
            "color": 0x2563EB, # Blue
            "fields": [
                {"name": "Priority Score", "value": f"**{article['priority_score']}**", "inline": True},
                {"name": "Source", "value": article['source'], "inline": True},
                {"name": "Pipelines", "value": ", ".join(article['category_tags']) or "None", "inline": False}
            ],
            "image": {"url": article.get('image_url')} if article.get('image_url') else None,
            "footer": {"text": "MediaRadar Realtime Intelligence Pipeline"}
        }]
    }
    
    # Adjust color for very high priority
    if article['priority_score'] >= 80:
        payload["embeds"][0]["color"] = 0xEA580C # Orange/Red
        payload["embeds"][0]["title"] = f"🔥 CRITICAL ALERT: {article['title']}"

    async with aiohttp.ClientSession() as session:
        for webhook_url in webhooks:
            try:
                async with session.post(webhook_url, json=payload) as resp:
                    if resp.status not in [200, 204]:
                        logger.error(f"Failed to send Discord alert to {webhook_url}: {resp.status}")
            except Exception as e:
                logger.error(f"Error sending Discord alert: {e}")
