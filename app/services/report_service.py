import pandas as pd
import os
from datetime import datetime, timedelta, timezone
from app.db.mongodb import db
import logging
import asyncio
from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

async def generate_article_report(days: int = 7, min_score: int = 0, tags: list = None) -> str:
    """Generates an Excel report of articles with advanced filtering."""
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    
    query = {
        "published_at": {"$gte": since},
        "priority_score": {"$gte": min_score}
    }
    
    if tags:
        query["category_tags"] = {"$in": tags}
    
    cursor = db.db.articles.find(query).sort("published_at", -1)
    articles = []
    async for doc in cursor:
        doc["_id"] = str(doc["_id"])
        articles.append(doc)
    
    if not articles:
        return ""
    
    df = pd.DataFrame(articles)
    
    # Clean up columns for report
    cols_to_keep = ['title', 'source', 'published_at', 'priority_score', 'category_tags', 'url', 'summary']
    df = df[df.columns.intersection(cols_to_keep)]
    
    # Rename for readability
    df.columns = [c.replace('_', ' ').title() for c in df.columns]
    
    filename = f"MediaRadar_Report_{now.strftime('%Y%m%d_%H%M')}.xlsx"
    filepath = os.path.join("temp", filename)
    os.makedirs("temp", exist_ok=True)
    
    df.to_excel(filepath, index=False)
    return filepath

async def send_report_to_discord(filepath: str, channel_tag: str = "Reports"):
    """Sends a generated file to Discord via the bot."""
    from app.services.discord_service import bot
    import disnake
    
    if not os.path.exists(filepath):
        logger.error(f"Report file not found: {filepath}")
        return
    
    # In a real scenario, you'd find a specific channel ID or use a webhook.
    # Since we have the bot instance, we can find a channel or send to the owner.
    
    try:
        # For simplicity in this implementation, we send to the main webhook 
        # or a specific channel if the bot is in a guild.
        # But webhooks are easier for files if we use aiohttp.
        
        import aiohttp
        from app.services.notification_service import get_webhook_for_tag
        
        webhook_url = await get_webhook_for_tag(channel_tag)
        if not webhook_url:
            logger.error("No webhook found for report")
            return
            
        async with aiohttp.ClientSession() as session:
            with open(filepath, 'rb') as f:
                form = aiohttp.FormData()
                form.add_field('file', f, filename=os.path.basename(filepath))
                form.add_field('content', f"📊 **MediaRadar Intelligence Report**\nGenerated at: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
                
                async with session.post(webhook_url, data=form) as resp:
                    if resp.status in (200, 204):
                        logger.info(f"Report sent to Discord: {os.path.basename(filepath)}")
                    else:
                        logger.error(f"Failed to send report: {await resp.text()}")
    finally:
        # Cleanup
        if os.path.exists(filepath):
            os.remove(filepath)

async def weekly_report_job():
    """Scheduled job for weekly Excel reports."""
    logger.info("Starting weekly scheduled report")
    filepath = await generate_article_report(days=7)
    if filepath:
        await send_report_to_discord(filepath, channel_tag="Reports")
    else:
        logger.info("No articles found for weekly report")
