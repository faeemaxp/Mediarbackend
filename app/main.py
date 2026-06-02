from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
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

@app.get("/health")
async def health():
    from app.services.discord_service import bot
    from app.core.config import get_settings
    settings = get_settings()
    
    db_ok = True
    try:
        from app.db.mongodb import db
        if db.db is not None:
            await db.db.command("ping")
        else:
            db_ok = False
    except Exception:
        db_ok = False

    discord_ready = bot.is_ready() if bot else False
    discord_ok = True
    if settings.DISCORD_TOKEN:
        discord_ok = discord_ready
        
    status = "ok" if (db_ok and discord_ok) else "degraded"
    
    return {
        "status": status,
        "database": "connected" if db_ok else "disconnected",
        "discord_bot": "ready" if discord_ready else ("not_configured" if not settings.DISCORD_TOKEN else "failed_or_connecting")
    }

@app.get("/wakeup", response_class=HTMLResponse)
async def wakeup():
    html_content = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>MediaRadar - Waking Up</title>
        <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;700&display=swap" rel="stylesheet">
        <style>
            body {
                background-color: #09090b;
                color: #f4f4f5;
                font-family: 'Outfit', sans-serif;
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                min-height: 100vh;
                margin: 0;
                overflow: hidden;
            }
            .container {
                text-align: center;
                max-width: 500px;
                padding: 40px;
                background: rgba(24, 24, 27, 0.4);
                backdrop-filter: blur(12px);
                border: 1px solid rgba(63, 63, 70, 0.3);
                border-radius: 24px;
                box-shadow: 0 20px 40px rgba(0,0,0,0.5);
            }
            .icon-container {
                display: flex;
                align-items: center;
                justify-content: center;
                width: 64px;
                height: 64px;
                background: #2563eb;
                border-radius: 20px;
                margin: 0 auto 24px auto;
                box-shadow: 0 0 30px rgba(37, 99, 235, 0.4);
                animation: pulse 2s infinite ease-in-out;
            }
            .icon {
                width: 32px;
                height: 32px;
                fill: currentColor;
            }
            h1 {
                font-size: 28px;
                font-weight: 700;
                margin: 0 0 12px 0;
                letter-spacing: -0.025em;
                background: linear-gradient(to right, #ffffff, #a1a1aa);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
            }
            p {
                color: #a1a1aa;
                font-size: 15px;
                line-height: 1.6;
                margin: 0 0 24px 0;
            }
            .loader {
                width: 200px;
                height: 4px;
                background: #27272a;
                border-radius: 2px;
                margin: 0 auto 24px auto;
                overflow: hidden;
                position: relative;
            }
            .loader-bar {
                width: 50%;
                height: 100%;
                background: #2563eb;
                position: absolute;
                border-radius: 2px;
                animation: loading 1.5s infinite ease-in-out;
            }
            .status {
                font-size: 12px;
                color: #71717a;
                text-transform: uppercase;
                letter-spacing: 0.1em;
                font-weight: 600;
            }
            @keyframes pulse {
                0%, 100% { transform: scale(1); box-shadow: 0 0 30px rgba(37, 99, 235, 0.4); }
                50% { transform: scale(1.05); box-shadow: 0 0 45px rgba(37, 99, 235, 0.6); }
            }
            @keyframes loading {
                0% { left: -50%; }
                100% { left: 100%; }
            }
        </style>
        <script>
            async function checkStatus() {
                try {
                    const res = await fetch('/health');
                    if (res.ok) {
                        const data = await res.json();
                        if (data.status === "ok") {
                            document.querySelector('h1').innerText = "Command Center Ready";
                            document.querySelector('p').innerText = "MediaRadar and its Discord bot have successfully booted up.";
                            document.querySelector('.loader-bar').style.width = "100%";
                            document.querySelector('.loader-bar').style.left = "0";
                            document.querySelector('.loader-bar').style.animation = "none";
                            document.querySelector('.status').innerText = "ONLINE";
                            document.querySelector('.status').style.color = "#10b981";
                            
                            const urlParams = new URLSearchParams(window.location.search);
                            const redirect = urlParams.get('redirect');
                            if (redirect) {
                                setTimeout(() => {
                                    window.location.href = redirect;
                                }, 1500);
                            }
                        } else {
                            // If degraded, display specific sub-service status
                            let detail = "Waking up services...";
                            if (data.database !== "connected") {
                                detail = "Connecting database...";
                            } else if (data.discord_bot === "failed_or_connecting") {
                                detail = "Starting Discord bot...";
                            }
                            document.querySelector('.status').innerText = detail;
                            setTimeout(checkStatus, 2000);
                        }
                    } else {
                        setTimeout(checkStatus, 2000);
                    }
                } catch(e) {
                    setTimeout(checkStatus, 2000);
                }
            }
            window.addEventListener('DOMContentLoaded', () => {
                setTimeout(checkStatus, 1000);
            });
        </script>
    </head>
    <body>
        <div class="container">
            <div class="icon-container">
                <svg class="icon" viewBox="0 0 24 24">
                    <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-6h2v6zm0-8h-2V7h2v2z"/>
                </svg>
            </div>
            <h1>Initializing MediaRadar</h1>
            <p>The Render server and Discord bot are waking up from hibernation. This may take a few moments...</p>
            <div class="loader">
                <div class="loader-bar"></div>
            </div>
            <div class="status">Connecting to service...</div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content, status_code=200)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
