from fastapi import APIRouter, HTTPException, Query
from typing import List, Optional
from backend.app.schemas.article import ArticleResponse
from backend.app.db.mongodb import db
from bson import ObjectId

router = APIRouter()

from datetime import datetime, timedelta, timezone

@router.get("/trending")
async def get_trending():
    # Last 24 hours
    since = datetime.now(timezone.utc) - timedelta(days=1)
    
    pipeline = [
        {"$match": {"published_at": {"$gte": since}}},
        {"$unwind": "$category_tags"},
        {"$group": {"_id": "$category_tags", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 10}
    ]
    
    cursor = db.db.articles.aggregate(pipeline)
    trending_categories = []
    async for doc in cursor:
        trending_categories.append({"name": doc["_id"], "count": doc["count"]})
        
    return {"categories": trending_categories}

@router.get("/", response_model=List[ArticleResponse])
async def get_articles(
    skip: int = 0, 
    limit: int = 20, 
    category: Optional[str] = None,
    source: Optional[str] = None,
    min_priority: Optional[int] = None,
    search: Optional[str] = None
):
    query = {}
    if category:
        query["category_tags"] = category
    if source:
        query["source"] = source
    if min_priority is not None:
        query["priority_score"] = {"$gte": min_priority}
    if search:
        query["$or"] = [
            {"title": {"$regex": search, "$options": "i"}},
            {"content": {"$regex": search, "$options": "i"}}
        ]
    
    # Always sort by published_at (newest first)
    # If High Priority mode is requested (min_priority), we can prepend that as a secondary or primary sort? 
    # User specifically said: "latest news in the filtered latest feed"
    # So published_at must be the primary sort key.
    sort_criteria = [("published_at", -1)]
    if min_priority is not None:
         sort_criteria = [("priority_score", -1), ("published_at", -1)]
        
    cursor = db.db.articles.find(query).sort(sort_criteria).skip(skip).limit(limit)
    articles = []
    async for doc in cursor:
        doc["id"] = str(doc.pop("_id"))
        articles.append(doc)
    return articles

@router.get("/{article_id}", response_model=ArticleResponse)
async def get_article(article_id: str):
    if not ObjectId.is_valid(article_id):
        raise HTTPException(status_code=400, detail="Invalid ID")
        
    doc = await db.db.articles.find_one({"_id": ObjectId(article_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Article not found")
        
    doc["id"] = str(doc.pop("_id"))
    return doc
