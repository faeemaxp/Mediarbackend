from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.db.mongodb import connect_to_mongo, close_mongo_connection

from app.core.scheduler import setup_scheduler
from app.services.discord_service import start_bot, stop_bot
import asyncio

@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = None
    discord_task = None
    try:
        await connect_to_mongo()
        scheduler = setup_scheduler()
        # Start Discord Bot in background
        discord_task = asyncio.create_task(start_bot())
    except Exception as e:
        import logging
        logging.warning(f"Failed to initialize services: {e}")
    
    yield
    
    try:
        if scheduler:
            scheduler.shutdown()
        if discord_task:
            await stop_bot()
            discord_task.cancel()
        await close_mongo_connection()
    except Exception as e:
        import logging
        logging.warning(f"Error during shutdown: {e}")

from app.api.articles import router as article_router
from app.api.sources import router as source_router
from app.api.admin import router as admin_router

import os

app = FastAPI(title="MediaRadar API", version="0.1.0", lifespan=lifespan)

app.include_router(article_router, prefix="/articles", tags=["articles"])
app.include_router(source_router, prefix="/sources", tags=["sources"])
app.include_router(admin_router, prefix="/admin", tags=["admin"])

# CORS Configuration
# Format: http://localhost:3000,https://mediaradar.vercel.app
raw_origins = os.getenv("CORS_ORIGINS", "*").split(",")
origins = [origin.strip().rstrip("/") for origin in raw_origins]

# Special handling for '*' with allow_credentials=True
# If origins is ['*'], we must set allow_credentials=False for security and compatibility
is_wildcard = "*" in origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=not is_wildcard,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return {"message": "Welcome to MediaRadar API"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
