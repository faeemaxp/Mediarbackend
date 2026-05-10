import typer
import asyncio
import httpx
import os
import sys
from tabulate import tabulate

# Add project root to sys.path to resolve the app package
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

app = typer.Typer()

ADMIN_TOKEN = "supersecret"
API_URL = "http://localhost:8000/admin"

@app.command()
def source_status():
    """List all sources and their health status."""
    headers = {"X-Admin-Token": ADMIN_TOKEN}
    try:
        response = httpx.get(f"{API_URL}/sources", headers=headers)
        response.raise_for_status()
        sources = response.json()
        
        table = []
        for s in sources:
            health = s.get("health", {})
            table.append([
                s["name"],
                "✅" if s["active"] else "❌",
                health.get("status", "unknown"),
                health.get("total_articles_ingested", 0),
                health.get("last_fetch", "Never")
            ])
            
        print(tabulate(table, headers=["Name", "Active", "Health", "Articles", "Last Fetch"]))
    except Exception as e:
        print(f"Error: {e}")

@app.command()
def scrape_now(source_id: str = None):
    """Force a scrape for all or a specific source."""
    headers = {"X-Admin-Token": ADMIN_TOKEN}
    url = f"{API_URL}/force-scrape"
    if source_id:
        url += f"?source_id={source_id}"
        
    try:
        response = httpx.post(url, headers=headers)
        response.raise_for_status()
        print(response.json()["message"])
    except Exception as e:
        print(f"Error: {e}")

@app.command()
def scrape_url(url: str):
    """Scrape a specific URL using newspaper3k and show details."""
    from newspaper import Article
    import nltk
    
    print(f"Scraping: {url}...")
    try:
        try:
            nltk.data.find('tokenizers/punkt_tab')
        except (LookupError, AttributeError):
            nltk.download('punkt', quiet=True)
            nltk.download('punkt_tab', quiet=True)
            
        article = Article(url)
        article.download()
        article.parse()
        article.nlp()
        
        print("\n--- Article Details ---")
        print(f"Title:    {article.title}")
        print(f"Authors:  {', '.join(article.authors)}")
        print(f"Date:     {article.publish_date}")
        print(f"Image:    {article.top_image}")
        print(f"Keywords: {', '.join(article.keywords)}")
        print("\n--- Summary ---")
        print(article.summary)
        print("\n--- Content (First 500 chars) ---")
        print(article.text[:500] + "...")
    except Exception as e:
        print(f"Error: {e}")

@app.command()
def retag_all():
    """Re-process all articles in the database to update tags and scores."""
    import asyncio
    
    async def run_retag():
        from app.db.mongodb import db, connect_to_mongo, close_mongo_connection
        from app.services.topic_service import detect_topics_and_score
        
        await connect_to_mongo()
        
        cursor = db.db.articles.find({})
        count = await db.db.articles.count_documents({})
        print(f"Retagging {count} articles...")
        
        updated = 0
        deleted = 0
        
        async for article in cursor:
            intelligence = detect_topics_and_score(article["title"], article["content"])
            
            # Use boolean flag to distinguish intelligence vs general news
            is_intelligence = len(intelligence["topics"]) > 0 or intelligence.get("score", 0) >= 15
            
            await db.db.articles.update_one(
                {"_id": article["_id"]},
                {"$set": {
                    "category_tags": intelligence["topics"],
                    "topic_relevance": intelligence.get("topic_relevance", {}),
                    "priority_score": intelligence["score"],
                    "people": intelligence["people"],
                    "organizations": intelligence["organizations"],
                    "is_intelligence": is_intelligence
                }}
            )
            updated += 1
            
        print(f"Done. Updated: {updated}")
        await close_mongo_connection()

    asyncio.run(run_retag())

@app.command()
def instagram_fetch(query: str = "#news", limit: int = 10):
    """Fetch trending posts from Instagram using Apify."""
    import asyncio
    
    async def run_fetch():
        from app.db.mongodb import connect_to_mongo, close_mongo_connection
        from app.services.instagram_service import fetch_instagram_news
        from dotenv import load_dotenv
        
        load_dotenv()
        await connect_to_mongo()
        
        print(f"Fetching {limit} latest trending posts for: {query}")
        articles = await fetch_instagram_news(query=query, limit=limit)
        
        print(f"Finished! Ingested {len(articles)} new articles.")
        for art in articles:
            print(f"- {art['title']} ({art['url']})")
            
        await close_mongo_connection()

    asyncio.run(run_fetch())

@app.command()
def x_fetch(limit: int = 10):
    """Fetch trending posts from right-wing media on X using Apify."""
    import asyncio
    
    async def run_fetch():
        from app.db.mongodb import connect_to_mongo, close_mongo_connection
        from app.services.x_service import fetch_x_news
        from dotenv import load_dotenv
        
        load_dotenv()
        await connect_to_mongo()
        
        print(f"Fetching {limit} latest posts from right-wing media on X...")
        articles = await fetch_x_news(limit=limit)
        
        print(f"Finished! Ingested {len(articles)} new articles.")
        for art in articles:
            print(f"- {art['title']} ({art['url']})")
            
        await close_mongo_connection()

    asyncio.run(run_fetch())

@app.command()
def push_latest_to_discord():
    """Push the single latest article for every tag to its Discord channel."""
    import asyncio
    
    async def run_push():
        from app.db.mongodb import connect_to_mongo, close_mongo_connection
        from app.services.notification_service import send_discord_alert
        from app.db.mongodb import db
        from dotenv import load_dotenv
        
        load_dotenv()
        await connect_to_mongo()
        
        TAGS = ["RSS", "BJP", "Congress", "Religion", "Election", "Geopolitics", "Politics", "Tamil", "General"]
        print("🚀 Pushing latest articles for every tag to Discord...")
        
        for tag in TAGS:
            query = {"category_tags": tag} if tag != "General" else {"$or": [{"category_tags": {"$size": 0}}, {"category_tags": "General"}]}
            article = await db.db.articles.find_one(query, sort=[("published_at", -1)])
            
            if article:
                print(f"Found latest for [{tag}]: {article['title']}")
                await send_discord_alert(article)
            else:
                print(f"ℹ️ No articles found for tag: {tag}")

        print("\n🏁 Push completed. Notifications queued.")
        await close_mongo_connection()

    asyncio.run(run_push())

@app.command()
def push_high_priority(limit: int = 10):
    """Push the top highest-scoring articles to Discord."""
    import asyncio
    
    async def run_push():
        from app.db.mongodb import connect_to_mongo, close_mongo_connection
        from app.services.notification_service import send_discord_alert
        from app.db.mongodb import db
        from dotenv import load_dotenv
        
        load_dotenv()
        await connect_to_mongo()
        
        print(f"🚀 Identifying top {limit} Elite Intelligence articles...")
        cursor = db.db.articles.find().sort("priority_score", -1).limit(limit)
        articles = await cursor.to_list(length=limit)
        
        for art in articles:
            print(f"Elite article [{art['priority_score']}]: {art['title']}")
            await send_discord_alert(art)
            
        print("\n🏁 Elite push completed. Notifications queued.")
        await close_mongo_connection()

    asyncio.run(run_push())

if __name__ == "__main__":
    app()
