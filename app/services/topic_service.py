from typing import List, Dict, Tuple, Set
import re

# Pipeline Configuration
PIPELINES = {
    "RSS": {
        "keywords": ["rss", "sangh", "sangh parivar", "rashtriya swayamsevak sangh", "hindutva", "hindu nationalism", "cultural nationalism", "shakha"],
        "individuals": ["mohan bhagwat", "dattatreya hosabale", "indresh kumar", "krishna gopal"],
        "organizations": ["vhp", "bajrang dal", "abvp", "sewa bharati", "vishwa hindu parishad"]
    },
    "BJP": {
        "keywords": ["bjp", "nda", "saffron party", "modi government", "governance", "lotus symbol"],
        "individuals": ["narendra modi", "amit shah", "jp nadda", "yogi adityanath", "nirmala sitharaman", "rajnath singh", "s jaishankar"],
        "organizations": ["bharatiya janata party", "nda alliance"]
    },
    "Congress": {
        "keywords": ["congress", "india alliance", "upa", "hand symbol", "nyay yatra"],
        "individuals": ["rahul gandhi", "sonia gandhi", "mallikarjun kharge", "priyanka gandhi", "shashi tharoor", "kc venugopal"],
        "organizations": ["inc", "indian national congress", "youth congress"]
    },
    "Religion": {
        "keywords": ["temple", "mosque", "religion", "communal", "conversion", "pilgrimage", "faith", "hindu", "muslim", "christian", "sikh", "waqf", "sanatana", "ayodhya", "kashi", "mathura"],
        "individuals": [],
        "organizations": []
    },
    "Election": {
        "keywords": ["election", "poll", "vote", "seat sharing", "campaign", "manifesto", "evm", "exit poll", "voter list", "byelection", "constituency"],
        "individuals": [],
        "organizations": ["election commission", "eci"]
    },
    "Geopolitics": {
        "keywords": ["china", "pakistan", "border", "foreign policy", "defence", "lac", "loc", "diplomacy", "quad", "brics", "g20"],
        "individuals": [],
        "organizations": ["mea", "ministry of external affairs"]
    },
    "Politics": {
        "keywords": ["politics", "political", "government", "cabinet", "parliament", "assembly", "legislation", "bill", "opposition", "leader", "minister", "mla", "mp", "governor", "democracy", "protest", "rally", "policy"],
        "individuals": [],
        "organizations": []
    },
    "Tamil": {
        "keywords": ["tamil nadu", "chennai", "dmk", "admk", "stalin", "annamalai", "eps", "ops", "puducherry", "dravidian", "cauvery", "jallikattu", "periyar", "தமிழ்நாடு", "சென்னை", "திமுக", "அதிமுக", "ஸ்டாலின்", "அண்ணாமலை", "எடப்பாடி பழனிசாமி", "ஓ. பன்னீர்செல்வம்", "பெரியார்", "திராவிட", "காவிரி"],
        "individuals": ["m.k. stalin", "uadhayanidhi stalin", "k. annamalai", "edappadi palaniswami", "o. panneerselvam", "ஸ்டாலின்", "அண்ணாமலை"],
        "organizations": ["dmk", "aiadmk", "ntk", "pmk", "vck", "திமுக", "அதிமுக"]
    }
}

# Keywords that boost priority significantly
HIGH_PRIORITY_KEYWORDS = [
    "breaking", "urgent", "exclusive", "alert", "crisis", "victory", "defeat", 
    "resigns", "protest", "clash", "violence", "investigation", "controversy",
    "scandal", "verdict", "arrest", "summons", "cbi", "ed", "income tax", "raid"
]

# Global Exclusions: If any of these are found, the article is likely not political intelligence
# This prevents sports, entertainment, or generic lifestyle news from leaking in.
IRRELEVANT_KEYWORDS = [
    "cricket", "ipl", "t20", "scorecard", "wicket", "stadium", "bollywood", "box office", 
    "horoscope", "zodiac", "recipe", "fashion", "lifestyle", "gadgets", "smartphone review",
    "gaming", "football", "tennis", "olympics", "misleading", "fake news alert", "viral video",
    "unboxing", "deals", "discount", "stock market live", "weather update", "astrology"
]

def detect_topics_and_score(title: str, content: str) -> Dict:
    text = f"{title} {content}".lower()
    title_lower = title.lower()
    
    # 1. Check for Global Exclusions FIRST
    for exclude in IRRELEVANT_KEYWORDS:
        if re.search(rf"\b{exclude}\b", text):
            return {
                "topics": [],
                "topic_relevance": {},
                "score": 0,
                "people": [],
                "organizations": []
            }

    detected_topics = []
    topic_relevance = {} 
    found_people = set()
    found_orgs = set()
    score = 0
    
    for pipeline_name, config in PIPELINES.items():
        matches = {
            "keywords": 0,
            "people": 0,
            "orgs": 0
        }
        
        pipeline_keyword_bonus = 0
        # Keyword matching with word boundaries
        for kw in config["keywords"]:
            if re.search(rf"\b{kw}\b", text):
                matches["keywords"] += 1
                if re.search(rf"\b{kw}\b", title_lower):
                    pipeline_keyword_bonus += 10
        
        pipeline_people_bonus = 0
        # Individual matching
        for person in config["individuals"]:
            if re.search(rf"\b{person}\b", text):
                matches["people"] += 1
                found_people.add(person.title())
                if re.search(rf"\b{person}\b", title_lower):
                    pipeline_people_bonus += 15
        
        pipeline_org_bonus = 0
        # Organization matching
        for org in config["organizations"]:
            if re.search(rf"\b{org}\b", text):
                matches["orgs"] += 1
                found_orgs.add(org.upper())
                if re.search(rf"\b{org}\b", title_lower):
                    pipeline_org_bonus += 15

        # Calculate pipeline-specific relevance
        if matches["keywords"] > 0 or matches["people"] > 0 or matches["orgs"] > 0:
            detected_topics.append(pipeline_name)
            
            relevance = 20 # Base match
            relevance += (matches["keywords"] * 5) + pipeline_keyword_bonus
            relevance += (matches["people"] * 15) + pipeline_people_bonus
            relevance += (matches["orgs"] * 10) + pipeline_org_bonus
            
            # Individual + Org overlap in same pipeline
            if matches["people"] > 0 and matches["orgs"] > 0:
                relevance += 20
                
            topic_relevance[pipeline_name] = min(relevance, 100)
            score += relevance

    # Cross-pipeline Overlap Scoring (Boosts total priority)
    # 1. RSS + Political entities
    if "RSS" in detected_topics and ("BJP" in detected_topics or "Congress" in detected_topics):
        score += 30
        
    # 2. Religion + Political overlap
    if "Religion" in detected_topics and any(t in detected_topics for t in ["BJP", "Congress", "RSS"]):
        score += 25
        
    # 3. Election + Anything
    if "Election" in detected_topics and len(detected_topics) > 1:
        score += 20

    # 4. Multi-category General Bonus
    if len(detected_topics) > 1:
        score += (len(detected_topics) * 5)
        
    # 5. High Priority Keyword Boosting
    for hpw in HIGH_PRIORITY_KEYWORDS:
        if re.search(rf"\b{re.escape(hpw)}\b", text):
            score += 25
            if re.search(rf"\b{re.escape(hpw)}\b", title_lower):
                score += 15

    return {
        "topics": detected_topics,
        "topic_relevance": topic_relevance,
        "score": min(score, 100), # Cap at 100
        "people": list(found_people),
        "organizations": list(found_orgs)
    }

async def process_article_topics(article_id: str, title: str, content: str):
    from app.db.mongodb import db
    from bson import ObjectId
    
    result = detect_topics_and_score(title, content)
    
    await db.db.articles.update_one(
        {"_id": ObjectId(article_id)},
        {"$set": {
            "category_tags": result["topics"],
            "topic_relevance": result["topic_relevance"],
            "priority_score": result["score"],
            "people": result["people"],
            "organizations": result["organizations"]
        }}
    )
    return result
