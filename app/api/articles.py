from fastapi import APIRouter, HTTPException, Query
from typing import List, Optional
from app.schemas.article import ArticleResponse
from app.db.mongodb import db
from bson import ObjectId
import re

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

@router.get("/briefing")
async def get_hourly_briefing():
    from app.services.briefing_service import generate_and_save_briefing
    content, created_at, edition = await generate_and_save_briefing()
    return {"briefing": content, "created_at": created_at, "edition": edition}

@router.get("/", response_model=List[ArticleResponse])
async def get_articles(
    skip: int = 0, 
    limit: int = 20, 
    category: Optional[str] = None,
    source: Optional[str] = None,
    min_priority: Optional[int] = None,
    search: Optional[str] = None,
    sort_by: str = "intelligence", # "time" or "priority" or "intelligence"
    only_saved: bool = False
):
    query = {}
    if category:
        query["category_tags"] = category
    if source:
        query["source"] = source
    if min_priority is not None:
        query["priority_score"] = {"$gte": min_priority}
    if only_saved:
        query["is_saved"] = True
    if search:
        safe_search = re.escape(search)  # SEC-04: prevent ReDoS via user-supplied regex
        query["$or"] = [
            {"title": {"$regex": safe_search, "$options": "i"}},
            {"content": {"$regex": safe_search, "$options": "i"}}
        ]
    
    # Sorting Logic
    if sort_by == "time":
        sort_criteria = [("published_at", -1)]
    elif sort_by == "priority":
        sort_criteria = [("priority_score", -1), ("published_at", -1)]
    else: # Default: intelligence (Layered)
        sort_criteria = [("is_intelligence", -1), ("priority_score", -1), ("published_at", -1)]
    
    if category and sort_by == "intelligence":
        # User requested a specific panel/tag and default sorting
        # Show the most RELEVANT to that tag first
        sort_criteria = [(f"topic_relevance.{category}", -1), ("priority_score", -1), ("published_at", -1)]
        
    cursor = db.db.articles.find(query).sort(sort_criteria).skip(skip).limit(limit)
    articles = []
    async for doc in cursor:
        doc["id"] = str(doc.pop("_id"))
        articles.append(doc)
    return articles

@router.post("/{article_id}/save")
async def save_article(article_id: str):
    if not ObjectId.is_valid(article_id):
        raise HTTPException(status_code=400, detail="Invalid ID")
        
    result = await db.db.articles.update_one(
        {"_id": ObjectId(article_id)},
        {"$set": {"is_saved": True}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Article not found")
    return {"message": "Article saved"}

@router.post("/{article_id}/unsave")
async def unsave_article(article_id: str):
    if not ObjectId.is_valid(article_id):
        raise HTTPException(status_code=400, detail="Invalid ID")
        
    result = await db.db.articles.update_one(
        {"_id": ObjectId(article_id)},
        {"$set": {"is_saved": False}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Article not found")
    return {"message": "Article removed from saved"}

@router.get("/{article_id}", response_model=ArticleResponse)
async def get_article(article_id: str):
    if not ObjectId.is_valid(article_id):
        raise HTTPException(status_code=400, detail="Invalid ID")
        
    doc = await db.db.articles.find_one({"_id": ObjectId(article_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Article not found")
        
    doc["id"] = str(doc.pop("_id"))
    return doc

@router.post("/{article_id}/research")
async def research_article(article_id: str):
    if not ObjectId.is_valid(article_id):
        raise HTTPException(status_code=400, detail="Invalid ID")
        
    article = await db.db.articles.find_one({"_id": ObjectId(article_id)})
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
        
    if article.get("ai_intelligence"):
        return {"ai_intelligence": article["ai_intelligence"]}
        
    from app.services.gemini_service import gemini_service
    ai_info = await gemini_service.get_ai_intelligence(article["title"], article["content"])
    
    if ai_info:
        await db.db.articles.update_one(
            {"_id": ObjectId(article_id)},
            {"$set": {"ai_intelligence": ai_info}}
        )
        return {"ai_intelligence": ai_info}
    else:
        raise HTTPException(status_code=500, detail="Failed to generate AI intelligence")
