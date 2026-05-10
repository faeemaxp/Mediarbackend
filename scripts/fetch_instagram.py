import asyncio
import os
import sys
from dotenv import load_dotenv

# Add parent directory to path to import app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.mongodb import connect_to_mongo, close_mongo_connection
from app.services.instagram_service import fetch_instagram_news

async def main():
    load_dotenv()
    await connect_to_mongo()
    
    query = "#news"
    if len(sys.argv) > 1:
        query = sys.argv[1]
        
    print(f"Fetching 10 latest trending posts for: {query}")
    articles = await fetch_instagram_news(query=query, limit=10)
    
    print(f"Finished! Ingested {len(articles)} new articles.")
    for art in articles:
        print(f"- {art['title']} ({art['url']})")
        
    await close_mongo_connection()

if __name__ == "__main__":
    asyncio.run(main())
