import asyncio
import os
import sys

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.db.mongodb import connect_to_mongo, close_mongo_connection, db

async def verify():
    await connect_to_mongo()
    art = await db.db.articles.find_one(sort=[('published_at', -1)])
    if art:
        print(f"Newest article title: {art.get('title')}, Date: {art.get('published_at')}")
    else:
        print("No articles found.")
    
    await close_mongo_connection()

if __name__ == "__main__":
    asyncio.run(verify())
