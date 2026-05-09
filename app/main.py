from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.app.db.mongodb import connect_to_mongo, close_mongo_connection

from backend.app.core.scheduler import setup_scheduler

@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_to_mongo()
    scheduler = setup_scheduler()
    yield
    scheduler.shutdown()
    await close_mongo_connection()

from backend.app.api.articles import router as article_router
from backend.app.api.sources import router as source_router
from backend.app.api.admin import router as admin_router

import os

app = FastAPI(title="MediaRadar API", version="0.1.0", lifespan=lifespan)

app.include_router(article_router, prefix="/articles", tags=["articles"])
app.include_router(source_router, prefix="/sources", tags=["sources"])
app.include_router(admin_router, prefix="/admin", tags=["admin"])

# CORS Configuration
# Format: http://localhost:3000,https://mediaradar.vercel.app
origins = os.getenv("CORS_ORIGINS", "*").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return {"message": "Welcome to MediaRadar API"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
