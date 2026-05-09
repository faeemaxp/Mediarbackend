import asyncio
import os
import sys

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.db.mongodb import connect_to_mongo, close_mongo_connection, db

async def verify():
    await connect_to_mongo()
    count = await db.db.sources.count_documents({})
    print(f"Total sources in DB: {count}")
    
    if count > 0:
        source = await db.db.sources.find_one()
        print(f"Sample source: {source['name']} ({source['rss_url']})")
    
    await close_mongo_connection()

if __name__ == "__main__":
    asyncio.run(verify())
