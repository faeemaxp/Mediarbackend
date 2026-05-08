from typing import List, Dict, Tuple
import re

# Priority categories and keywords
POLITICAL_CATEGORIES = {
    "BJP": ["modi", "bjp", "amit shah", "nda", "saffron party", "yogi adityanath", "jp nadda"],
    "RSS": ["rss", "sangh", "sangh parivar", "rashtriya swayamsevak sangh", "mohan bhagwat", "shakha"],
    "Congress": ["congress", "rahul gandhi", "sonia gandhi", "priyanka gandhi", "upa", "kharge"],
    "Religion": ["hindu", "muslim", "temple", "religion", "mosque", "church", "faith", "religious", "sanatana"],
    "Election": ["election", "poll", "vote", "seat", "constituency", "evm", "exit poll", "voter"]
}

# Keywords that boost priority significantly
HIGH_PRIORITY_KEYWORDS = ["breaking", "urgent", "exclusive", "alert", "crisis", "victory", "defeat", "resigns", "protest"]

def detect_topics_and_score(title: str, content: str) -> Tuple[List[str], int]:
    text = f"{title} {content}".lower()
    detected_topics = []
    score = 0
    
    # 1. Category Detection & Base Scoring
    for category, keywords in POLITICAL_CATEGORIES.items():
        matches = 0
        for kw in keywords:
            if kw in text:
                matches += 1
        
        if matches > 0:
            detected_topics.append(category)
            score += 20  # Base score for category match
            score += (matches * 5)  # Bonus for multiple keyword hits in same category
            
    # 2. Multi-category Bonus
    if len(detected_topics) > 1:
        score += (len(detected_topics) * 10)
        
    # 3. High Priority Keyword Boosting
    for hpw in HIGH_PRIORITY_KEYWORDS:
        if hpw in text:
            score += 25
            
    # 4. Title Weighting (Keywords in title are more important)
    title_lower = title.lower()
    for category, keywords in POLITICAL_CATEGORIES.items():
        for kw in keywords:
            if kw in title_lower:
                score += 15
                
    return detected_topics, score

async def process_article_topics(article_id: str, title: str, content: str):
    from backend.app.db.mongodb import db
    from bson import ObjectId
    
    topics, score = detect_topics_and_score(title, content)
    
    await db.db.articles.update_one(
        {"_id": ObjectId(article_id)},
        {"$set": {
            "category_tags": topics,
            "priority_score": score
        }}
    )
    return topics, score
