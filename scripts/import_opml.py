import xml.etree.ElementTree as ET
import asyncio
import os
import sys
from datetime import datetime, timezone

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.db.mongodb import connect_to_mongo, close_mongo_connection, db
from app.schemas.source import SourceCreate

async def import_opml(file_path: str):
    print(f"Parsing {file_path}...")
    tree = ET.parse(file_path)
    root = tree.getroot()
    
    sources = []
    # Find all outlines with xmlUrl
    for outline in root.findall(".//outline[@xmlUrl]"):
        name = outline.get("title") or outline.get("text")
        rss_url = outline.get("xmlUrl")
        
        if not name or not rss_url:
            continue
            
        source = {
            "name": name,
            "rss_url": rss_url,
            "url": None, # Optional now
            "category": "general",
            "active": True,
            "created_at": datetime.now(timezone.utc)
        }
        sources.append(source)
    
    print(f"Found {len(sources)} sources. Connecting to database...")
    await connect_to_mongo()
    
    count = 0
    for source in sources:
        # Check for duplicates
        existing = await db.db.sources.find_one({"rss_url": source["rss_url"]})
        if not existing:
            await db.db.sources.insert_one(source)
            print(f"Imported: {source['name']}")
            count += 1
        else:
            print(f"Skipped (exists): {source['name']}")
            
    await close_mongo_connection()
    print(f"Done! Imported {count} new sources.")

if __name__ == "__main__":
    opml_file = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "sources.opml"))
    asyncio.run(import_opml(opml_file))
