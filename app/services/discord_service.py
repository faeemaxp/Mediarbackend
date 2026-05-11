import disnake
from disnake.ext import commands
import logging
import asyncio
import aiohttp
import os
from datetime import datetime, timezone
from typing import Optional
from app.core.config import get_settings
from app.core.scheduler import fetch_all_feeds, fetch_x_job
from app.db.mongodb import db

logger = logging.getLogger(__name__)
settings = get_settings()

# All pipeline tags — used for slash-command autocomplete
TAGS = ["RSS", "BJP", "Congress", "Religion", "Election", "Geopolitics", "Politics", "Tamil"]

# Tag → emoji for richer embeds
TAG_EMOJI = {
    "RSS": "🕉️",
    "BJP": "🪷",
    "Congress": "✋",
    "Religion": "⛪",
    "Election": "🗳️",
    "Geopolitics": "🌏",
    "Politics": "🏛️",
    "Tamil": "🎌",
    "General": "📡",
}


# ---------------------------------------------------------------------------
# Shared embed builder for bot-command responses
# ---------------------------------------------------------------------------
def create_article_embed(art: dict) -> disnake.Embed:
    score = art.get("priority_score", 0)

    if score >= 90:
        color = disnake.Color.red()
    elif score >= 75:
        color = disnake.Color.orange()
    elif score >= 50:
        color = disnake.Color(0xF59E0B)  # Amber
    else:
        color = disnake.Color.blue()

    description = art.get("summary") or art.get("content", "")[:300]
    if len(description) > 300:
        description = description[:300] + "…"

    embed = disnake.Embed(
        title=art["title"][:256],
        url=art["url"],
        description=description,
        color=color,
        timestamp=datetime.now(timezone.utc),
    )

    if art.get("image_url"):
        embed.set_image(url=art["image_url"])

    embed.add_field(name="📊 Score",  value=f"**{score}/100**",      inline=True)
    embed.add_field(name="📰 Source", value=art.get("source", "?"),   inline=True)

    tags = art.get("category_tags", [])
    if tags:
        tag_str = "  ".join(
            f"{TAG_EMOJI.get(t, '🏷️')} {t}" for t in tags
        )
        embed.add_field(name="Pipelines", value=tag_str, inline=False)

    people = art.get("people", [])
    if people:
        embed.add_field(name="👤 People", value=", ".join(people[:4]), inline=False)

    embed.set_footer(text="MediaRadar Intelligence  •  /latest to see more")
    return embed


# ---------------------------------------------------------------------------
# Interactive View — used ONLY by bot slash commands (user-invoked)
# Has Save + Research buttons. NOT sent in webhook notifications.
# ---------------------------------------------------------------------------
class IntelligenceView(disnake.ui.View):
    def __init__(self, article_id: str, source_url: str):
        super().__init__(timeout=300)  # 5 min timeout
        self.article_id = article_id
        # Link button added first so it appears left-most
        self.add_item(disnake.ui.Button(label="Read More", url=source_url, emoji="📖", row=0))

    @disnake.ui.button(label="Save Intelligence", style=disnake.ButtonStyle.secondary, emoji="💾", row=0)
    async def save_intel(self, interaction: disnake.MessageInteraction, button: disnake.ui.Button):
        from bson import ObjectId
        try:
            await db.db.articles.update_one(
                {"_id": ObjectId(self.article_id)},
                {"$set": {"is_saved": True, "saved_at": datetime.now(timezone.utc)}},
            )
            button.label = "✅ Saved"
            button.style = disnake.ButtonStyle.success
            button.disabled = True
            await interaction.response.edit_message(view=self)
        except Exception as exc:
            logger.error(f"Save button error: {exc}")
            await interaction.response.send_message("❌ Failed to save.", ephemeral=True)


# ---------------------------------------------------------------------------
# Pagination view for /saves
# ---------------------------------------------------------------------------
class SavedIntelligenceView(disnake.ui.View):
    def __init__(self, skip: int = 0):
        super().__init__(timeout=120)
        self.skip = skip
        self.limit = 3

    @disnake.ui.button(label="Show More ➕", style=disnake.ButtonStyle.primary)
    async def show_more(self, interaction: disnake.MessageInteraction, button: disnake.ui.Button):
        new_skip = self.skip + self.limit
        cursor = (
            db.db.articles
            .find({"is_saved": True})
            .sort("published_at", -1)
            .skip(new_skip)
            .limit(self.limit)
        )
        articles = await cursor.to_list(length=self.limit)

        if not articles:
            button.disabled = True
            button.label = "No more saves"
            await interaction.response.edit_message(view=self)
            return

        for art in articles:
            embed = create_article_embed(art)
            view = IntelligenceView(str(art["_id"]), art["url"])
            await interaction.channel.send(embed=embed, view=view)

        self.skip = new_skip
        if len(articles) < self.limit:
            button.disabled = True
            button.label = "All saves shown"

        await interaction.response.edit_message(view=self)


# ---------------------------------------------------------------------------
# Bot class
# ---------------------------------------------------------------------------
class DiscordBot(commands.Bot):
    def __init__(self):
        intents = disnake.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents, test_guilds=None)

    async def on_ready(self):
        logger.info(f"✅ Discord bot ready: {self.user} (ID: {self.user.id})")
        # Auto-configure on first boot
        await self._auto_setup()

    async def _auto_setup(self):
        """
        On startup, check if webhooks are already stored in DB.
        If not, automatically create the MEDIA RADAR category,
        all pipeline channels, and their webhooks — then save to DB.
        """
        if not self.guilds:
            logger.warning("[AutoSetup] Bot is not in any guild yet — skipping auto-setup.")
            return

        # Check if any webhooks already exist in DB
        existing = await db.db.config_webhooks.count_documents({})
        if existing > 0:
            logger.info(f"[AutoSetup] {existing} webhooks already configured — skipping auto-setup.")
            return

        guild = self.guilds[0]  # Use the first guild the bot is in
        logger.info(f"[AutoSetup] Starting auto-setup in guild: {guild.name}")

        all_tags = TAGS + ["General", "Reports", "Briefings"]
        results = []

        try:
            # Create or find MEDIA RADAR category
            category = disnake.utils.get(guild.categories, name="MEDIA RADAR")
            if not category:
                category = await guild.create_category("MEDIA RADAR")
                logger.info("[AutoSetup] Created MEDIA RADAR category")

            for tag in all_tags:
                channel_name = tag.lower().replace(" ", "-")
                channel = disnake.utils.get(category.text_channels, name=channel_name)

                if not channel:
                    channel = await guild.create_text_channel(
                        channel_name, category=category,
                        topic=f"MediaRadar auto-feed for the {tag} intelligence pipeline"
                    )
                    logger.info(f"[AutoSetup] Created #{channel_name}")

                # Create or reuse webhook
                existing_hooks = await channel.webhooks()
                hook = disnake.utils.get(existing_hooks, name=f"MediaRadar-{tag}")
                if not hook:
                    hook = await channel.create_webhook(name=f"MediaRadar-{tag}")

                # Save to DB
                await db.db.config_webhooks.update_one(
                    {"tag": tag},
                    {"$set": {
                        "tag": tag,
                        "webhook_url": hook.url,
                        "channel_id": str(channel.id),
                        "updated_at": datetime.now(timezone.utc),
                    }},
                    upsert=True,
                )
                results.append(f"{TAG_EMOJI.get(tag, '🏷️')} #{channel_name}")

            logger.info(f"[AutoSetup] ✅ Done! Configured: {', '.join(results)}")

        except Exception as exc:
            logger.error(f"[AutoSetup] Failed: {exc}", exc_info=True)

    async def on_slash_command_error(
        self,
        inter: disnake.ApplicationCommandInteraction,
        error: Exception,
    ):
        if isinstance(error, commands.CheckFailure):
            msg = str(error)
        else:
            logger.error(f"Slash command error in /{inter.data.name}: {error}", exc_info=True)
            msg = f"❌ Error: {error}"
            
        try:
            if not inter.response.is_done():
                await inter.response.send_message(msg, ephemeral=True)
            else:
                await inter.edit_original_response(content=msg)
        except Exception:
            pass

    async def on_command_error(self, ctx: commands.Context, error: Exception):
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.CheckFailure):
            await ctx.send(str(error))
            return
            
        logger.error(f"Prefix command error: {error}")
        await ctx.send(f"❌ Error: {error}")


bot = DiscordBot()


# ---------------------------------------------------------------------------
# Global Authentication Check
# ---------------------------------------------------------------------------

async def is_authorized(user_id: int) -> bool:
    doc = await db.db.bot_users.find_one({"user_id": str(user_id)})
    return bool(doc)

@bot.check
async def global_prefix_check(ctx: commands.Context):
    if ctx.command and ctx.command.name == "auth":
        return True
    
    if not await is_authorized(ctx.author.id):
        raise commands.CheckFailure("❌ Access Denied. Use `!auth <admin_token>` to authenticate.")
    return True

@bot.application_command_check()
async def global_slash_check(inter: disnake.ApplicationCommandInteraction):
    if not await is_authorized(inter.author.id):
        raise commands.CheckFailure("❌ Access Denied. You are not authenticated. Use `!auth <admin_token>`.")
    return True

@bot.command(name="auth")
async def prefix_auth(ctx: commands.Context, token: str = None):
    """One-time authentication to use the bot."""
    if not token:
        await ctx.send("❌ Usage: `!auth <admin_token>`")
        return
        
    if token != settings.ADMIN_TOKEN:
        await ctx.send("❌ Invalid token.")
        return
        
    await db.db.bot_users.update_one(
        {"user_id": str(ctx.author.id)},
        {"$set": {"user_id": str(ctx.author.id), "authenticated_at": datetime.now(timezone.utc)}},
        upsert=True
    )
    
    try:
        await ctx.message.delete()
    except Exception:
        pass
        
    await ctx.send("✅ Authentication successful! You now have full access to MediaRadar.")


# ---------------------------------------------------------------------------
# Prefix Commands
# ---------------------------------------------------------------------------

@bot.command(name="setup")
@commands.has_permissions(manage_channels=True)
async def prefix_setup(ctx: commands.Context):
    """Setup all channels and webhooks automatically (force re-run)."""
    msg = await ctx.send("🚀 Starting automatic pipeline setup...")
    
    # Capture the admin's User ID to ping them on urgent alerts
    user_id = str(ctx.author.id)
    settings.NOTIFICATION_USER_ID = user_id
    
    # Update .env file safely
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            lines = f.readlines()
        with open(env_path, "w") as f:
            found = False
            for line in lines:
                if line.startswith("NOTIFICATION_USER_ID="):
                    f.write(f"NOTIFICATION_USER_ID={user_id}\n")
                    found = True
                else:
                    f.write(line)
            if not found:
                f.write(f"\nNOTIFICATION_USER_ID={user_id}\n")
    
    try:
        existing = await db.db.config_webhooks.count_documents({})
        if existing > 0:
            await msg.edit(content=f"⚠️ {existing} webhooks already in DB. Re-running to sync...")

        category = disnake.utils.get(ctx.guild.categories, name="MEDIA RADAR")
        if not category:
            category = await ctx.guild.create_category("MEDIA RADAR")

        # Include the new Reports and Briefings tags
        all_tags = TAGS + ["General", "Reports", "Briefings"]
        results = []

        for tag in all_tags:
            channel_name = tag.lower().replace(" ", "-")
            channel = disnake.utils.get(category.text_channels, name=channel_name)

            if not channel:
                channel = await ctx.guild.create_text_channel(
                    channel_name, category=category,
                    topic=f"MediaRadar auto-feed for the {tag} intelligence pipeline"
                )

            existing_hooks = await channel.webhooks()
            hook = disnake.utils.get(existing_hooks, name=f"MediaRadar-{tag}")
            if not hook:
                hook = await channel.create_webhook(name=f"MediaRadar-{tag}")

            await db.db.config_webhooks.update_one(
                {"tag": tag},
                {
                    "$set": {
                        "tag": tag,
                        "webhook_url": hook.url,
                        "channel_id": str(channel.id),
                        "updated_at": datetime.now(timezone.utc),
                    }
                },
                upsert=True,
            )
            results.append(f"{TAG_EMOJI.get(tag, '🏷️')} #{channel_name}")

        summary = "\n".join(results)
        await msg.edit(content=f"✅ **Intelligence Pipeline Ready!**\n{summary}")

    except Exception as exc:
        await msg.edit(content=f"❌ Setup failed: {exc}")


@bot.command(name="setwebhook")
@commands.has_permissions(administrator=True)
async def prefix_setwebhook(ctx: commands.Context, tag: str, url: str):
    """Manually set a webhook for a specific tag. Usage: !setwebhook Reports <url>"""
    await db.db.config_webhooks.update_one(
        {"tag": tag},
        {"$set": {"tag": tag, "webhook_url": url, "updated_at": datetime.now(timezone.utc)}},
        upsert=True
    )
    await ctx.send(f"✅ Webhook for `#{tag}` updated successfully.")


@bot.command(name="prefix")
@commands.has_permissions(administrator=True)
async def prefix_change(ctx: commands.Context, new_prefix: str):
    """Change the bot's command prefix."""
    bot.command_prefix = new_prefix
    await ctx.send(f"✅ Command prefix changed to `{new_prefix}`")


# ---------------------------------------------------------------------------
# Tag autocomplete helper
# ---------------------------------------------------------------------------
async def tag_autocomplete(inter: disnake.ApplicationCommandInteraction, input: str):
    return [t for t in TAGS if input.lower() in t.lower()]


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------

@bot.slash_command(description="Check bot latency")
async def ping(inter: disnake.ApplicationCommandInteraction):
    await inter.response.send_message(
        f"🏓 Pong! Latency: **{round(bot.latency * 1000)}ms**", ephemeral=True
    )


@bot.slash_command(description="Manually trigger a news fetch from all active sources")
async def fetch_news(inter: disnake.ApplicationCommandInteraction):
    await inter.response.defer()
    try:
        await fetch_all_feeds()
        await fetch_x_job()
        await inter.edit_original_response(content="✅ News fetch completed for all sources.")
    except Exception as exc:
        logger.error(f"Manual fetch error: {exc}")
        await inter.edit_original_response(content=f"❌ Fetch failed: {exc}")


@bot.slash_command(description="Get the latest intelligence briefing (AI-generated)")
async def briefing(inter: disnake.ApplicationCommandInteraction, force: bool = False):
    """
    Parameters
    ----------
    force: Regenerate even if a current briefing already exists.
    """
    await inter.response.defer()
    try:
        from app.services.briefing_service import generate_and_save_briefing
        content, created_at, edition = await generate_and_save_briefing(force=force)

        embed = disnake.Embed(
            title=f"📡 Intelligence Brief — {(edition or 'General').capitalize()} Edition",
            color=disnake.Color.gold(),
            timestamp=created_at if isinstance(created_at, datetime) else datetime.now(timezone.utc),
        )

        # Discord embed description limit is 4096 chars
        if len(content) > 4000:
            embed.description = content[:4000] + "\n\n…*(truncated)*"
        else:
            embed.description = content

        embed.set_footer(text="Synthesized by Gemini 2.0 Flash  •  MediaRadar")
        await inter.edit_original_response(embed=embed)

    except Exception as exc:
        logger.error(f"Briefing error: {exc}", exc_info=True)
        await inter.edit_original_response(content=f"❌ Briefing failed: {exc}")


@bot.slash_command(description="Get the latest intelligence reports")
async def latest(
    inter: disnake.ApplicationCommandInteraction,
    tag: str = commands.Param(
        default=None,
        description="Filter by pipeline tag",
        autocomplete=tag_autocomplete,
    ),
    limit: int = commands.Param(default=3, ge=1, le=10, description="Number of articles (1–10)"),
    sort: str = commands.Param(
        default="intelligence",
        choices=["intelligence", "priority", "time"],
        description="Sort order",
    ),
):
    await inter.response.defer()

    query: dict = {}
    if tag:
        query["category_tags"] = tag

    sort_map = {
        "time":        [("published_at", -1)],
        "priority":    [("priority_score", -1), ("published_at", -1)],
        "intelligence":[("is_intelligence", -1), ("priority_score", -1), ("published_at", -1)],
    }
    sort_criteria = sort_map.get(sort, sort_map["intelligence"])

    cursor = db.db.articles.find(query).sort(sort_criteria).limit(limit)
    articles = await cursor.to_list(length=limit)

    if not articles:
        label = f" in **{tag}**" if tag else ""
        await inter.edit_original_response(
            content=f"No intelligence found{label}. Try running `/fetch-news` first."
        )
        return

    for art in articles:
        embed = create_article_embed(art)
        view = IntelligenceView(str(art["_id"]), art["url"])
        await inter.channel.send(embed=embed, view=view)

    tag_label = f" · `#{tag}`" if tag else ""
    await inter.edit_original_response(
        content=f"📋 Showing **{len(articles)}** latest reports{tag_label} (sorted by {sort})"
    )


@bot.slash_command(description="View your saved intelligence bookmarks")
async def saves(inter: disnake.ApplicationCommandInteraction):
    await inter.response.defer()

    cursor = (
        db.db.articles.find({"is_saved": True})
        .sort("saved_at", -1)
        .limit(3)
    )
    articles = await cursor.to_list(length=3)

    if not articles:
        await inter.edit_original_response(
            content="📂 You have no saved intelligence. Use **Save Intelligence** button on any article."
        )
        return

    for art in articles:
        embed = create_article_embed(art)
        view = IntelligenceView(str(art["_id"]), art["url"])
        await inter.channel.send(embed=embed, view=view)

    total = await db.db.articles.count_documents({"is_saved": True})
    more_view = SavedIntelligenceView(skip=0) if total > 3 else None
    await inter.edit_original_response(
        content=f"💾 Showing **3** of **{total}** saved articles.",
        view=more_view,
    )


@bot.slash_command(description="Search articles by keyword")
async def search(
    inter: disnake.ApplicationCommandInteraction,
    query: str = commands.Param(description="Search keywords"),
    limit: int = commands.Param(default=3, ge=1, le=5, description="Results (1–5)"),
):
    await inter.response.defer()
    import re
    safe = re.escape(query)
    db_query = {
        "$or": [
            {"title":   {"$regex": safe, "$options": "i"}},
            {"content": {"$regex": safe, "$options": "i"}},
        ]
    }
    cursor = db.db.articles.find(db_query).sort("priority_score", -1).limit(limit)
    articles = await cursor.to_list(length=limit)

    if not articles:
        await inter.edit_original_response(content=f'🔍 No results for **"{query}"**.')
        return

    for art in articles:
        embed = create_article_embed(art)
        view = IntelligenceView(str(art["_id"]), art["url"])
        await inter.channel.send(embed=embed, view=view)

    await inter.edit_original_response(
        content=f'🔍 Found **{len(articles)}** result(s) for **"{query}"**'
    )


@bot.slash_command(description="Show pipeline statistics")
async def stats(inter: disnake.ApplicationCommandInteraction):
    await inter.response.defer(ephemeral=True)
    try:
        total = await db.db.articles.count_documents({})
        saved = await db.db.articles.count_documents({"is_saved": True})
        high  = await db.db.articles.count_documents({"priority_score": {"$gte": 75}})
        sources_count = await db.db.sources.count_documents({"active": True})

        embed = disnake.Embed(
            title="📊 MediaRadar Pipeline Stats",
            color=disnake.Color.blue(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="📰 Total Articles", value=f"**{total:,}**",    inline=True)
        embed.add_field(name="💾 Saved",          value=f"**{saved:,}**",    inline=True)
        embed.add_field(name="⚠️ High Priority",  value=f"**{high:,}**",     inline=True)
        embed.add_field(name="📡 Active Sources", value=f"**{sources_count}**", inline=True)

        # Per-tag breakdown
        pipeline_lines = []
        for tag in TAGS:
            count = await db.db.articles.count_documents({"category_tags": tag})
            emoji = TAG_EMOJI.get(tag, "🏷️")
            pipeline_lines.append(f"{emoji} **{tag}**: {count:,}")

        embed.add_field(
            name="Pipeline Breakdown",
            value="\n".join(pipeline_lines),
            inline=False,
        )
        embed.set_footer(text="MediaRadar Intelligence")
        await inter.edit_original_response(embed=embed)

    except Exception as exc:
        logger.error(f"Stats command error: {exc}", exc_info=True)
        await inter.edit_original_response(content=f"❌ Failed to fetch stats: {exc}")


@bot.slash_command(
    name="setup-intelligence",
    description="Create MEDIA RADAR channels & webhooks for each pipeline tag",
)
async def setup_intelligence(inter: disnake.ApplicationCommandInteraction):
    await inter.response.defer()

    if not inter.author.guild_permissions.manage_channels:
        await inter.edit_original_response(content="❌ You need **Manage Channels** permission.")
        return

    try:
        category = disnake.utils.get(inter.guild.categories, name="MEDIA RADAR")
        if not category:
            category = await inter.guild.create_category("MEDIA RADAR")

        all_tags = TAGS + ["General"]
        results = []

        for tag in all_tags:
            channel_name = tag.lower().replace(" ", "-")
            channel = disnake.utils.get(category.text_channels, name=channel_name)

            if not channel:
                channel = await inter.guild.create_text_channel(
                    channel_name, category=category,
                    topic=f"MediaRadar auto-feed for the {tag} intelligence pipeline"
                )

            # Create or reuse webhook
            existing_hooks = await channel.webhooks()
            hook = disnake.utils.get(existing_hooks, name=f"MediaRadar-{tag}")
            if not hook:
                hook = await channel.create_webhook(name=f"MediaRadar-{tag}")

            # Store in DB so notification_service can look it up
            await db.db.config_webhooks.update_one(
                {"tag": tag},
                {
                    "$set": {
                        "tag": tag,
                        "webhook_url": hook.url,
                        "channel_id": str(channel.id),
                        "updated_at": datetime.now(timezone.utc),
                    }
                },
                upsert=True,
            )
            results.append(f"{TAG_EMOJI.get(tag, '🏷️')} #{channel_name}")

        summary = "\n".join(results)
        await inter.edit_original_response(
            content=f"🚀 **Intelligence Pipeline Ready!**\n{summary}"
        )

    except Exception as exc:
        logger.error(f"setup-intelligence error: {exc}", exc_info=True)
        await inter.edit_original_response(content=f"❌ Setup failed: {exc}")


# ---------------------------------------------------------------------------
# Bot lifecycle (called from FastAPI lifespan in main.py)
# ---------------------------------------------------------------------------
async def start_bot():
    if not settings.DISCORD_TOKEN:
        logger.warning("DISCORD_TOKEN not set — Discord bot will not start.")
        return
    try:
        await bot.start(settings.DISCORD_TOKEN)
    except Exception as exc:
        logger.error(f"Discord bot failed to start: {exc}", exc_info=True)


async def stop_bot():
    if not bot.is_closed():
        await bot.close()
