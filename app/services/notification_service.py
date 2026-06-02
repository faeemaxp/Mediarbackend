"""
notification_service.py — MediaRadar Discord Notification System

Architecture:
  1. LIVE ALERT  — triggered per article at ingest time.
                   Routes to ONE primary channel (highest topic_relevance score).
                   Never sends the same article to multiple channels.

  2. CHANNEL DIGEST — periodic job (every ~45 min) per channel.
                      Style is randomly picked each run to feel "alive":
                        • BREAKING — single highest-priority recent article
                        • DIGEST   — last 4 articles, compact list format
                        • ROUNDUP  — top 5 by score in last 8 h, ranked table
"""

import aiohttp
import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Optional
from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
HOURLY_CAP = 12          # Reduced to 12/hour (one every 5 mins avg) to prevent overwhelm
MIN_GAP_SECONDS = 120    # At least 2 minutes between any two alerts
MAX_GAP_SECONDS = 300    # Max 5 minutes wait
MAX_QUEUE_SIZE = 15     # If more than 15 articles are waiting, we drop the oldest/lowest priority
DIGEST_COOLDOWN = 1800   # min seconds between digests for the same channel (30 min)

TAG_EMOJI = {
    "RSS":        "🕉️",
    "BJP":        "🪷",
    "Congress":   "✋",
    "Religion":   "⛪",
    "Election":   "🗳️",
    "Geopolitics": "🌏",
    "Politics":   "🏛️",
    "Tamil":      "🎌",
    "General":    "📡",
    "Reports":    "📊",
    "Briefings":  "🧠",
}

# ---------------------------------------------------------------------------
# Rate-limiting state (live alerts)
# ---------------------------------------------------------------------------
notification_queue: asyncio.Queue = asyncio.Queue()
last_sent_time = datetime.min.replace(tzinfo=timezone.utc)
hourly_counter = 0
hour_reset_time = datetime.now(timezone.utc)


# ===========================================================================
# PART 1 — Primary-channel resolver
# ===========================================================================

def _get_primary_tag(article: dict) -> str:
    """
    Return the SINGLE most-relevant pipeline tag for an article.
    Uses topic_relevance scores when available so the article always
    goes to the channel it matters most in — never duplicated.
    """
    tags = article.get("category_tags", [])
    if not tags:
        return "General"

    relevance = article.get("topic_relevance", {})
    if relevance:
        return max(tags, key=lambda t: relevance.get(t, 0))

    return tags[0]   # fallback: first tag


async def get_webhook_for_tag(tag: str) -> Optional[str]:
    """
    Resolve a webhook URL for a tag.
    Priority: DB config_webhooks → .env map → default webhook.
    """
    from app.db.mongodb import db

    row = await db.db.config_webhooks.find_one({"tag": tag})
    if row and row.get("webhook_url"):
        return row["webhook_url"]

    env_map = {
        "RSS":        settings.RSS_WEBHOOK_URL,
        "BJP":        settings.BJP_WEBHOOK_URL,
        "Congress":   settings.CONGRESS_WEBHOOK_URL,
        "Religion":   settings.RELIGION_WEBHOOK_URL,
        "Election":   settings.ELECTION_WEBHOOK_URL,
        "Geopolitics": settings.GEOPOLITICS_WEBHOOK_URL,
        "Reports":    settings.REPORTS_WEBHOOK_URL,
        "Briefings":  settings.BRIEFINGS_WEBHOOK_URL,
    }
    if tag in env_map and env_map[tag]:
        return env_map[tag]

    # Reports and Briefings NEVER fall back to the general webhook
    if tag in ("Reports", "Briefings"):
        return None

    return settings.DISCORD_WEBHOOK_URL


# ===========================================================================
# PART 2 — Embed builders (three distinct visual styles)
# ===========================================================================

def _link_row(url: str, label: str = "Read Full Article") -> dict:
    """Single-button action row with a plain link (no custom_id needed)."""
    return {
        "type": 1,
        "components": [{
            "type": 2, "style": 5,
            "label": label,
            "url": url,
            "emoji": {"name": "📰"},
        }],
    }


def _build_live_alert_payload(article: dict, tag: str) -> dict:
    """
    LIVE ALERT style — single article, full detail embed.
    Colour and prefix shift with priority score.
    Called once per ingest for one channel only.
    """
    score = article.get("priority_score", 0)

    if score >= 90:
        color, prefix = 0xDC2626, "🔥 CRITICAL ALERT"
    elif score >= 75:
        color, prefix = 0xEA580C, "⚠️ HIGH PRIORITY"
    elif score >= 50:
        color, prefix = 0xF59E0B, "⚡ PRIORITY INTEL"
    else:
        color, prefix = 0x2563EB, "📡 NEW INTEL"

    # Use only prefix for the message content (no user mentions)
    content = prefix

    desc = (article.get("summary") or article.get("content", ""))[:480]
    if len(desc) == 480:
        desc += "…"

    fields = [
        {"name": "📊 Score",    "value": f"**{score}/100**",             "inline": True},
        {"name": "📰 Source",   "value": article.get("source", "?"),     "inline": True},
        {"name": "🎯 Pipeline", "value": f"`#{tag}`",                     "inline": True},
    ]

    # AI Enhancement: Quick Intel field
    if article.get("ai_blurb"):
        fields.insert(0, {
            "name": "🧠 Quick Intel",
            "value": f"*{article['ai_blurb']}*",
            "inline": False
        })

    all_tags = article.get("category_tags", [])
    if len(all_tags) > 1:
        fields.append({
            "name": "🏷️ Also tagged",
            "value": " · ".join(f"`{t}`" for t in all_tags if t != tag),
            "inline": False,
        })

    people = article.get("people", [])
    if people:
        fields.append({"name": "👤 Key People",
                       "value": ", ".join(people[:5]), "inline": False})

    embed = {
        "title": f"{prefix}: {article['title'][:240]}",
        "url": article["url"],
        "description": desc,
        "color": color,
        "fields": fields,
        "footer": {"text": f"#{tag} Live Alert  •  MediaRadar"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if article.get("image_url"):
        embed["image"] = {"url": article["image_url"]}

    return {"content": content, "embeds": [embed], "components": [_link_row(article["url"])]}


def _build_breaking_payload(article: dict, tag: str) -> dict:
    """
    BREAKING digest style — dramatic single-article callout.
    Used by the periodic digest when picking highest-priority article.
    Visually distinct from live alerts: larger banner text, no score field clutter.
    """
    score = article.get("priority_score", 0)
    emoji = TAG_EMOJI.get(tag, "📡")

    if score >= 80:
        color, banner = 0xDC2626, f"🚨 {emoji} **BREAKING** — {tag} Pipeline"
    elif score >= 60:
        color, banner = 0xEA580C, f"⚡ {emoji} **SPOTLIGHT** — {tag} Pipeline"
    else:
        color, banner = 0xF59E0B, f"📌 {emoji} **FEATURED** — {tag} Pipeline"

    desc = (article.get("summary") or article.get("content", ""))[:520]
    if len(desc) == 520:
        desc += "…"

    embed = {
        "title": article["title"][:256],
        "url": article["url"],
        "description": desc,
        "color": color,
        "fields": [
            {"name": "📰 Source", "value": article.get("source", "?"), "inline": True},
            {"name": "📊 Score",  "value": f"**{score}/100**",         "inline": True},
        ],
        "footer": {"text": f"#{tag} Digest · Breaking  •  MediaRadar"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if article.get("image_url"):
        embed["thumbnail"] = {"url": article["image_url"]}

    return {"content": banner, "embeds": [embed], "components": [_link_row(article["url"])]}


def _build_digest_payload(articles: list, tag: str) -> dict:
    """
    DIGEST style — compact numbered list of 3–4 latest articles.
    Each article gets a coloured dot by score and a clickable link.
    """
    emoji = TAG_EMOJI.get(tag, "📡")
    fields = []

    for i, art in enumerate(articles, 1):
        score = art.get("priority_score", 0)
        dot = "🔴" if score >= 75 else "🟡" if score >= 50 else "🔵"
        pub = art.get("published_at")
        time_str = ""
        if pub:
            if isinstance(pub, str):
                try:
                    pub = datetime.fromisoformat(pub.replace("Z", "+00:00"))
                except Exception:
                    pub = None
            if pub:
                mins_ago = int((datetime.now(timezone.utc) - pub).total_seconds() / 60)
                time_str = f" · {mins_ago}m ago" if mins_ago < 60 else f" · {mins_ago//60}h ago"

        fields.append({
            "name": f"{dot}  {i}. {art['title'][:75]}",
            "value": (
                f"**{art.get('source', '?')}**{time_str} · Score **{score}**\n"
                f"[→ Read Article]({art['url']})"
            ),
            "inline": False,
        })

    embed = {
        "title": f"{emoji} Latest from #{tag}",
        "description": (
            f"**{len(articles)}** newest reports from the **{tag}** intelligence pipeline."
        ),
        "color": 0x2563EB,
        "fields": fields,
        "footer": {"text": f"#{tag} Digest · Latest  •  MediaRadar"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    return {"content": "", "embeds": [embed]}


def _build_roundup_payload(articles: list, tag: str) -> dict:
    """
    ROUNDUP style — priority-ranked table for the top stories of the past window.
    Purple accent, numbered by rank, avg score shown.
    """
    emoji = TAG_EMOJI.get(tag, "📡")

    lines = []
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
    for i, art in enumerate(articles):
        medal = medals[i] if i < len(medals) else f"`{i+1}.`"
        score = art.get("priority_score", 0)
        title = art["title"][:65]
        lines.append(f"{medal} [{title}]({art['url']}) — **{score}pts**")

    avg = sum(a.get("priority_score", 0) for a in articles) // max(len(articles), 1)
    top_score = max(a.get("priority_score", 0) for a in articles)

    embed = {
        "title": f"📊 Priority Roundup — #{tag}",
        "description": "\n\n".join(lines),
        "color": 0x7C3AED,   # Purple — visually distinct from alerts and digests
        "fields": [
            {"name": "📰 Stories",    "value": str(len(articles)), "inline": True},
            {"name": "⭐ Avg Score", "value": str(avg),            "inline": True},
            {"name": "🔝 Top Score", "value": str(top_score),      "inline": True},
        ],
        "footer": {"text": f"#{tag} Digest · Priority Roundup  •  MediaRadar"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    return {"content": "", "embeds": [embed]}


# ===========================================================================
# PART 3 — Live-alert queue worker
# ===========================================================================

async def _notification_worker():
    global last_sent_time, hourly_counter, hour_reset_time

    while True:
        # Each item: (article, webhook_url, tag)
        article, webhook_url, tag = await notification_queue.get()

        try:
            now = datetime.now(timezone.utc)
            
            # SMART OVERWHELM PROTECTION:
            # If the queue is getting large, drop articles with score < 60
            if notification_queue.qsize() > 5 and article.get("priority_score", 0) < 60:
                logger.info(f"[SmartStagger] Dropping low-priority article to clear backlog: {article['title'][:50]}")
                notification_queue.task_done()
                continue

            # Hourly counter reset & BACKLOG DROP
            if now >= hour_reset_time + timedelta(hours=1):
                logger.info(f"[SmartStagger] Hourly reset reached. Dropping {notification_queue.qsize()} stale articles from backlog.")
                # Flush the queue
                while not notification_queue.empty():
                    try:
                        notification_queue.get_nowait()
                        notification_queue.task_done()
                    except asyncio.QueueEmpty:
                        break
                
                hourly_counter = 0
                hour_reset_time = now

            if hourly_counter >= HOURLY_CAP:
                logger.warning(
                    f"[Notifications] Hourly cap ({HOURLY_CAP}) reached — "
                    f"skipping live alert: {article['title'][:60]}"
                )
                notification_queue.task_done()
                continue

            # RELAXED STAGGERING:
            # We wait a random amount of time between MIN and MAX gap
            # to make the notifications feel "loose" and less like a bot blast.
            wait_time = random.randint(MIN_GAP_SECONDS, MAX_GAP_SECONDS)
            elapsed = (now - last_sent_time).total_seconds()
            
            if elapsed < wait_time:
                sleep_for = wait_time - elapsed
                logger.info(f"[SmartStagger] Staggering next alert for {int(sleep_for)}s to prevent overwhelm...")
                await asyncio.sleep(sleep_for)

            # AI ENHANCEMENT: Generate a punchy blurb for critical items (score >= 90) to cut down Gemini API costs
            score = article.get("priority_score", 0)
            if score >= 90 and not article.get("ai_blurb"):
                try:
                    from app.services.gemini_service import gemini_service
                    # Pass summary or truncated content to save on API token costs
                    short_content = article.get("summary", "")
                    if not short_content:
                        short_content = article.get("content", "")[:500]
                        
                    blurb = await gemini_service.generate_notification_blurb(
                        article["title"], short_content, tag
                    )
                    if blurb:
                        article["ai_blurb"] = blurb
                except Exception as e:
                    logger.warning(f"Failed to generate AI blurb: {e}")

            payload = _build_live_alert_payload(article, tag)

            async with aiohttp.ClientSession() as session:
                async with session.post(webhook_url, json=payload) as resp:
                    if resp.status in (200, 204):
                        logger.info(
                            f"[#{tag}] Live alert sent: {article['title'][:60]} "
                            f"(score={article.get('priority_score', 0)}, "
                            f"hourly {hourly_counter + 1}/{HOURLY_CAP})"
                        )
                    elif resp.status == 429:
                        data = await resp.json()
                        wait = float(data.get("retry_after", 5))
                        logger.warning(f"Rate-limited by Discord — retrying in {wait}s")
                        await asyncio.sleep(wait)
                        await notification_queue.put((article, webhook_url, tag))
                        notification_queue.task_done()
                        continue
                    else:
                        body = await resp.text()
                        logger.error(f"Webhook error {resp.status} for #{tag}: {body[:200]}")

            last_sent_time = datetime.now(timezone.utc)
            hourly_counter += 1

        except Exception as exc:
            logger.error(f"Notification worker error: {exc}", exc_info=True)
        finally:
            notification_queue.task_done()


_worker_task: Optional[asyncio.Task] = None


def ensure_worker_started():
    global _worker_task
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.create_task(_notification_worker())


# ===========================================================================
# PART 4 — Live-alert public entry point  (called from rss_service / x_service)
# ===========================================================================

async def send_discord_alert(article: dict, bypass_stagger: bool = False):
    """
    Route a freshly ingested article as a LIVE ALERT to exactly ONE channel —
    the channel whose tag has the highest topic_relevance score.
    This prevents the same article from appearing in multiple channels.
    """
    ensure_worker_started()

    primary_tag = _get_primary_tag(article)
    webhook_url = await get_webhook_for_tag(primary_tag)

    # Final fallback: general webhook
    if not webhook_url:
        webhook_url = settings.DISCORD_WEBHOOK_URL

    if not webhook_url:
        logger.warning(
            f"[Notifications] No webhook for #{primary_tag} — "
            f"skipped: {article['title'][:60]}"
        )
        from app.db.mongodb import db
        await db.db.logs.insert_one({
            "timestamp": datetime.now(timezone.utc),
            "level": "WARNING",
            "message": "Live alert skipped: no webhook",
            "details": {"title": article["title"], "primary_tag": primary_tag},
        })
        return

    if bypass_stagger:
        try:
            score = article.get("priority_score", 0)
            if score >= 90 and not article.get("ai_blurb"):
                try:
                    from app.services.gemini_service import gemini_service
                    short_content = article.get("summary", "")
                    if not short_content:
                        short_content = article.get("content", "")[:500]
                    blurb = await gemini_service.generate_notification_blurb(
                        article["title"], short_content, primary_tag
                    )
                    if blurb:
                        article["ai_blurb"] = blurb
                except Exception as e:
                    logger.warning(f"Failed to generate AI blurb: {e}")

            payload = _build_live_alert_payload(article, primary_tag)
            async with aiohttp.ClientSession() as session:
                async with session.post(webhook_url, json=payload) as resp:
                    if resp.status in (200, 204):
                        logger.info(f"[#{primary_tag}] Direct live alert sent (bypassed stagger): {article['title'][:60]}")
                    else:
                        body = await resp.text()
                        logger.error(f"Direct webhook error {resp.status} for #{primary_tag}: {body[:200]}")
        except Exception as e:
            logger.error(f"Failed to send direct alert: {e}", exc_info=True)
        return

    await notification_queue.put((article, webhook_url, primary_tag))
    logger.info(
        f"[Notifications] Live alert queued → #{primary_tag}: {article['title'][:60]}"
    )


# ===========================================================================
# PART 5 — Periodic channel digest  (called from scheduler every ~45 min)
# ===========================================================================

DIGEST_STYLES = ["breaking", "digest", "roundup"]


async def send_channel_digest(tag: str, webhook_url: str):
    """
    Send a randomly-styled digest to a single channel.
    Respects DIGEST_COOLDOWN so no channel is spammed.

    Style selection:
      • breaking  — single highest-priority article (dramatic callout)
      • digest    — last 4 articles, compact list
      • roundup   — top 5 by score in last 8 h, ranked table

    If the preferred style yields no data it automatically falls back
    through the others until one works.
    """
    from app.db.mongodb import db

    # Cooldown guard per channel
    state = await db.db.digest_state.find_one({"tag": tag})
    if state and state.get("last_digest"):
        last = state["last_digest"]
        if isinstance(last, str):
            last = datetime.fromisoformat(last.replace("Z", "+00:00"))
        elapsed = (datetime.now(timezone.utc) - last).total_seconds()
        if elapsed < DIGEST_COOLDOWN:
            logger.debug(
                f"[Digest] #{tag} cooldown active "
                f"({int((DIGEST_COOLDOWN - elapsed) / 60)}min left) — skipping"
            )
            return

    # Shuffle styles so each channel gets a different order each run
    styles = DIGEST_STYLES.copy()
    random.shuffle(styles)

    payload = None
    chosen_style = None

    for style in styles:
        if style == "breaking":
            # Highest priority article from last 12 hours
            since = datetime.now(timezone.utc) - timedelta(hours=12)
            art = await db.db.articles.find_one(
                {"category_tags": tag,
                 "published_at": {"$gte": since},
                 "priority_score": {"$gte": 50}},
                sort=[("priority_score", -1)],
            )
            if art:
                payload = _build_breaking_payload(art, tag)
                chosen_style = "breaking"
                break

        elif style == "digest":
            # 4 most recent articles for this tag
            since = datetime.now(timezone.utc) - timedelta(hours=6)
            cursor = (
                db.db.articles
                .find({"category_tags": tag, "published_at": {"$gte": since}})
                .sort("published_at", -1)
                .limit(4)
            )
            arts = await cursor.to_list(4)
            if arts:
                payload = _build_digest_payload(arts, tag)
                chosen_style = "digest"
                break

        elif style == "roundup":
            # Top 5 by priority in last 8 hours
            since = datetime.now(timezone.utc) - timedelta(hours=8)
            cursor = (
                db.db.articles
                .find({"category_tags": tag, "published_at": {"$gte": since}})
                .sort("priority_score", -1)
                .limit(5)
            )
            arts = await cursor.to_list(5)
            if len(arts) >= 2:          # roundup needs at least 2 to make sense
                payload = _build_roundup_payload(arts, tag)
                chosen_style = "roundup"
                break

    # Hard fallback: latest 3 articles regardless of time window
    if not payload:
        cursor = (
            db.db.articles
            .find({"category_tags": tag})
            .sort("published_at", -1)
            .limit(3)
        )
        arts = await cursor.to_list(3)
        if arts:
            payload = _build_digest_payload(arts, tag)
            chosen_style = "fallback-digest"

    if not payload:
        logger.info(f"[Digest] #{tag} — no articles found, skipping digest")
        return

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(webhook_url, json=payload) as resp:
                if resp.status in (200, 204):
                    await db.db.digest_state.update_one(
                        {"tag": tag},
                        {"$set": {
                            "tag": tag,
                            "last_digest": datetime.now(timezone.utc),
                            "last_style": chosen_style,
                        }},
                        upsert=True,
                    )
                    logger.info(f"[Digest] #{tag} → style={chosen_style} sent ✓")
                elif resp.status == 429:
                    data = await resp.json()
                    logger.warning(
                        f"[Digest] #{tag} rate-limited — retry_after={data.get('retry_after')}s"
                    )
                else:
                    body = await resp.text()
                    logger.error(f"[Digest] #{tag} error {resp.status}: {body[:200]}")
    except Exception as exc:
        logger.error(f"[Digest] #{tag} exception: {exc}", exc_info=True)
async def send_briefing_notification(content: str, edition: str):
    """
    Sends a formatted AI Intelligence Briefing to the #Briefings channel.
    """
    webhook_url = await get_webhook_for_tag("Briefings")
    if not webhook_url:
        logger.warning("No webhook configured for #Briefings channel")
        return

    emoji = TAG_EMOJI.get("Briefings", "🧠")
    
    embed = {
        "title": f"{emoji} Intelligence Briefing — {edition.title()} Edition",
        "description": content[:4000],  # Discord limit
        "color": 0x3B82F6,  # Blue
        "footer": {"text": f"MediaRadar Intelligence Synthesis · {edition.title()}"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    
    async with aiohttp.ClientSession() as session:
        await session.post(webhook_url, json={"embeds": [embed]})


async def send_all_digests():
    """
    Iterate every configured channel in DB and send a varied digest.
    Called by the scheduler every 45 minutes.
    Each channel independently checks its own cooldown, so they naturally
    get staggered over time.
    """
    from app.db.mongodb import db

    try:
        cursor = db.db.config_webhooks.find({})
        async for config in cursor:
            tag = config.get("tag")
            webhook_url = config.get("webhook_url")
            if not tag or not webhook_url:
                continue
            try:
                await send_channel_digest(tag, webhook_url)
                # Small stagger between channels to avoid simultaneous POSTs
                await asyncio.sleep(3)
            except Exception as exc:
                logger.error(f"[Digest] Error for #{tag}: {exc}", exc_info=True)
    except Exception as exc:
        logger.error(f"[Digest] send_all_digests failed: {exc}", exc_info=True)
