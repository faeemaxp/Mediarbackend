from fastapi import APIRouter, HTTPException
from typing import List
from app.schemas.source import SourceCreate, SourceResponse
from app.db.mongodb import db
from app.services.rss_service import fetch_rss_feed
from bson import ObjectId
from datetime import datetime, timezone

router = APIRouter()

@router.post("/", response_model=SourceResponse)
async def create_source(source: SourceCreate):
    source_dict = source.model_dump()
    source_dict["created_at"] = datetime.now(timezone.utc)
    result = await db.db.sources.insert_one(source_dict)
    source_dict["id"] = str(result.inserted_id)
    return source_dict

@router.get("/", response_model=List[SourceResponse])
async def get_sources():
    cursor = db.db.sources.find()
    sources = []
    async for doc in cursor:
        doc["id"] = str(doc.pop("_id"))
        sources.append(doc)
    return sources

@router.post("/{source_id}/fetch")
async def trigger_fetch(source_id: str):
    if not ObjectId.is_valid(source_id):
        raise HTTPException(status_code=400, detail="Invalid ID")
        
    source = await db.db.sources.find_one({"_id": ObjectId(source_id)})
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
        
    if not source.get("rss_url"):
        raise HTTPException(status_code=400, detail="Source has no RSS URL")
        
    articles = await fetch_rss_feed(source["rss_url"], source["name"])
    return {"message": f"Fetched {len(articles)} articles", "articles": len(articles)}
