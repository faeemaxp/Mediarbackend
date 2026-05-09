import asyncio
import os
import sys

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.db.mongodb import db, connect_to_mongo, close_mongo_connection
from datetime import datetime

async def check_db():
    await connect_to_mongo()
    
    print("--- Sources Status ---")
    sources = await db.db.sources.find().to_list(100)
    for s in sources:
        last_fetch = s.get("health", {}).get("last_fetch")
        if isinstance(last_fetch, datetime):
            last_fetch = last_fetch.isoformat()
        print(f"Source: {s['name']}, Active: {s.get('active')}, Last Fetch: {last_fetch}, Status: {s.get('health', {}).get('status')}")
    
    print("\n--- Articles Count ---")
    count = await db.db.articles.count_documents({})
    print(f"Total articles: {count}")
    
    print("\n--- Latest 5 Articles ---")
    latest = await db.db.articles.find().sort("published_at", -1).limit(5).to_list(5)
    for a in latest:
        pub_at = a.get("published_at")
        if isinstance(pub_at, datetime):
            pub_at = pub_at.isoformat()
        has_summary = "Yes" if a.get("summary") else "No"
        print(f"Title: {a['title'][:50]}..., Published: {pub_at}, Summary: {has_summary}")

    await close_mongo_connection()

if __name__ == "__main__":
    asyncio.run(check_db())
