import asyncio
import os
import sys

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.db.mongodb import connect_to_mongo, close_mongo_connection, db

async def cleanup():
    await connect_to_mongo()
    
    # Check count before deletion
    count_before = await db.db.articles.count_documents({"source": "System Mock Ingest"})
    print(f"Found {count_before} mock articles in the database.")
    
    if count_before > 0:
        result = await db.db.articles.delete_many({"source": "System Mock Ingest"})
        print(f"Successfully deleted {result.deleted_count} mock articles.")
    else:
        print("No mock articles to delete.")
        
    await close_mongo_connection()

if __name__ == "__main__":
    asyncio.run(cleanup())
