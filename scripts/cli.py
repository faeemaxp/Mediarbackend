import typer
import asyncio
import httpx
import os
from tabulate import tabulate

app = typer.Typer()

ADMIN_TOKEN = "supersecret"
API_URL = "http://localhost:8000/admin"

@app.command()
def source_status():
    """List all sources and their health status."""
    headers = {"X-Admin-Token": ADMIN_TOKEN}
    try:
        response = httpx.get(f"{API_URL}/sources", headers=headers)
        response.raise_for_status()
        sources = response.json()
        
        table = []
        for s in sources:
            health = s.get("health", {})
            table.append([
                s["name"],
                "✅" if s["active"] else "❌",
                health.get("status", "unknown"),
                health.get("total_articles_ingested", 0),
                health.get("last_fetch", "Never")
            ])
            
        print(tabulate(table, headers=["Name", "Active", "Health", "Articles", "Last Fetch"]))
    except Exception as e:
        print(f"Error: {e}")

@app.command()
def scrape_now(source_id: str = None):
    """Force a scrape for all or a specific source."""
    headers = {"X-Admin-Token": ADMIN_TOKEN}
    url = f"{API_URL}/force-scrape"
    if source_id:
        url += f"?source_id={source_id}"
        
    try:
        response = httpx.post(url, headers=headers)
        response.raise_for_status()
        print(response.json()["message"])
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    app()
