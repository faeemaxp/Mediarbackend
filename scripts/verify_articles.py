import asyncio
import os
import sys

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.app.db.mongodb import connect_to_mongo, close_mongo_connection, db

async def verify():
    await connect_to_mongo()
    count = await db.db.articles.count_documents({})
    print(f"Total articles in DB: {count}")
    
    if count > 0:
        cursor = db.db.articles.find().limit(5)
        async for article in cursor:
            print(f"- {article['title']} ({article['source']})")
    
    await close_mongo_connection()

if __name__ == "__main__":
    asyncio.run(verify())
