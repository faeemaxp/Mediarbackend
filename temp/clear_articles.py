import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
import os
from dotenv import load_dotenv

load_dotenv()

async def main():
    mongo_url = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
    db_name = os.getenv("DATABASE_NAME", "mediaradar")
    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    
    # Delete non-saved articles
    res = await db.articles.delete_many({"is_saved": {"$ne": True}})
    print(f"Deleted {res.deleted_count} non-saved articles.")
    
    # Reset last_fetch for sources to trigger full re-scrape
    res_s = await db.sources.update_many({}, {"$unset": {"health.last_fetch": ""}})
    print(f"Reset last_fetch for sources to trigger fresh scrape.")

if __name__ == "__main__":
    asyncio.run(main())
