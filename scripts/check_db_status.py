import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
import json
from datetime import datetime

async def check_db():
    client = AsyncIOMotorClient("mongodb://localhost:27017")
    db = client["mediaradar"]
    
    print("--- Sources Status ---")
    sources = await db.sources.find().to_list(100)
    for s in sources:
        last_fetch = s.get("health", {}).get("last_fetch")
        if isinstance(last_fetch, datetime):
            last_fetch = last_fetch.isoformat()
        print(f"Source: {s['name']}, Active: {s.get('active')}, Last Fetch: {last_fetch}, Status: {s.get('health', {}).get('status')}")
    
    print("\n--- Articles Count ---")
    count = await db.articles.count_documents({})
    print(f"Total articles: {count}")
    
    print("\n--- Latest 5 Articles ---")
    latest = await db.articles.find().sort("published_at", -1).limit(5).to_list(5)
    for a in latest:
        pub_at = a.get("published_at")
        if isinstance(pub_at, datetime):
            pub_at = pub_at.isoformat()
        print(f"Title: {a['title'][:50]}..., Published: {pub_at}")

    client.close()

if __name__ == "__main__":
    asyncio.run(check_db())
