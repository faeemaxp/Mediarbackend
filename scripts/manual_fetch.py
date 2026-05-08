import asyncio
import os
import sys
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.app.core.scheduler import fetch_all_feeds
from backend.app.db.mongodb import connect_to_mongo, close_mongo_connection

async def manual_fetch():
    print("Connecting to database...")
    await connect_to_mongo()
    print("Starting manual RSS fetch for all sources...")
    await fetch_all_feeds()
    print("Fetch complete. Closing connection...")
    await close_mongo_connection()

if __name__ == "__main__":
    asyncio.run(manual_fetch())
