import os
import uuid
import discord
from discord import app_commands
from discord.ext import commands
from db import init_db, execute, fetchall, fetchone
from time_utils import parse_to_utc_iso, fmt_time
from views import AnnouncementContentModal, AnnouncementEditModal, RideView
from dashboard import render_dashboard
from dashboard_paginator import DashboardPaginator
from scheduler import scheduler_loop, delete_announcement
from dotenv import load_dotenv
load_dotenv()

PUBLIC_CHANNEL_ID = int(os.getenv("PUBLIC_CHANNEL_ID"))
ADMIN_CHANNEL_ID = int(os.getenv("ADMIN_CHANNEL_ID"))
ALLOWED_ROLE_ID = int(os.getenv("ALLOWED_ROLE_ID"))

intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    await init_db()

    # Restoring views for sent and closed announcements for persistence
    rows = await fetchall(
        "SELECT id, state, title, end_at, dashboard_page, reactable FROM announcements WHERE state IN ('sent', 'closed')"
    )

    for aid, state, title, end_at, page, reactable in rows:
        if reactable:
            bot.add_view(RideView(aid, is_closed=(state == "closed")))
            embeds = await render_dashboard(bot, aid, title, end_at)
            if embeds:
                bot.add_view(DashboardPaginator(embeds, aid, title, start_index=page))

    # Sync commands
    guild = discord.Object(id=1458080506158518343)
    bot.tree.copy_global_to(guild=guild)
    await bot.tree.sync(guild=guild)
    
    # Start scheduler loop
    bot.loop.create_task(scheduler_loop(bot))


# ─────────────────────────────────────────────────────────────
# Creates a scheduled announcement
# Format time like 'YYYY-MM-DD HH:MM' in US/Eastern.
# ─────────────────────────────────────────────────────────────
@app_commands.default_permissions(manage_messages=True)
@bot.tree.command(name="announcement_create")
async def create(
    interaction: discord.Interaction,
    title: str,
    send_at: str,
    end_at: str,
    reactable: bool,
):
    aid = str(uuid.uuid4())

    try:
        send_at_dt = parse_to_utc_iso(send_at)
    except Exception:
        await interaction.response.send_message(
            "Invalid `send_at` format. Use exactly: 'YYYY-MM-DD HH:MM' in US/Eastern (e.g. 2026-01-06 15:30).",
            ephemeral=True
        )
        return

    try:
        end_at_dt = parse_to_utc_iso(end_at)
    except Exception:
        await interaction.response.send_message(
            "Invalid `end_at` format. Use exactly: 'YYYY-MM-DD HH:MM' in US/Eastern (e.g. 2026-01-06 16:30).",
            ephemeral=True
        )
        return

    if end_at_dt < send_at_dt:
        await interaction.response.send_message(
            "`end_at` must be the same as or after `send_at`.",
            ephemeral=True
        )
        return
    
    await interaction.response.send_modal(
        AnnouncementContentModal(
            interaction=interaction,
            aid=aid,
            title=title,
            send_at_dt=send_at_dt,
            end_at_dt=end_at_dt,
            reactable=reactable,
        )
    )


# ─────────────────────────────────────────────────────────────
# Edits a sent announcement
# ─────────────────────────────────────────────────────────────
@app_commands.default_permissions(manage_messages=True)
@bot.tree.command(name="announcement_edit")
async def announcement_edit(
    interaction: discord.Interaction,
    announcement_id: str
):
    try:
        announcement_id = uuid.UUID(announcement_id)
    except ValueError:
        await interaction.response.send_message(
            "❌ Invalid announcement ID. Please provide a valid ID.",
            ephemeral=True
        )
        return
    row = await fetchone(
        """
        SELECT title, content, state
        FROM announcements
        WHERE id=$1
        """,
        (announcement_id,)
    )

    if not row:
        await interaction.response.send_message(
            "❌ Announcement not found.",
            ephemeral=True
        )
        return

    title, content, state = row

    if state == "scheduled":
        await interaction.response.send_message(
            "❌ Only announcements that have already been sent can be edited.",
            ephemeral=True
        )
        return

    await interaction.response.send_modal(
        AnnouncementEditModal(
            announcement_id=announcement_id,
            old_title=title,
            old_content=content,
        )
    )

# ─────────────────────────────────────────────────────────────
# Deletes an already-posted announcement
# ─────────────────────────────────────────────────────────────
@app_commands.default_permissions(manage_messages=True)
@bot.tree.command(name="announcement_delete")
async def announcement_delete(
    interaction: discord.Interaction,
    announcement_id: str
):
    try:
        announcement_id = uuid.UUID(announcement_id)
    except ValueError:
        await interaction.response.send_message(
            "❌ Invalid announcement ID. Please provide a valid ID.",
            ephemeral=True
        )
        return
    row = await fetchone(
        "SELECT state FROM announcements WHERE id=$1",
        (announcement_id,)
    )

    if not row or row[0] == "scheduled":
        await interaction.response.send_message(
            "❌ Only already-sent announcements can be deleted.",
            ephemeral=True
        )
        return

    successful = await delete_announcement(
        interaction.client,
        announcement_id
    )

    await interaction.response.send_message(
        f"✅ Announcement deleted successfully" if successful else f"❌ Announcement not found.",
        ephemeral=True
    )


# ─────────────────────────────────────────────────────────────
# Unschedules a scheduled announcement
# ─────────────────────────────────────────────────────────────
@app_commands.default_permissions(manage_messages=True)
@bot.tree.command(name="announcement_unschedule")
async def announcement_unschedule(
    interaction: discord.Interaction,
    announcement_id: str
):
    try:
        announcement_id = uuid.UUID(announcement_id)
    except ValueError:
        await interaction.response.send_message(
            "❌ Invalid announcement ID. Please provide a valid ID.",
            ephemeral=True
        )
        return
    row = await fetchone(
        "SELECT state FROM announcements WHERE id=$1",
        (announcement_id,)
    )

    if not row:
        await interaction.response.send_message(
            "❌ Announcement not found.",
            ephemeral=True
        )
        return

    if row[0] != "scheduled":
        await interaction.response.send_message(
            "❌ Only scheduled announcements can be unscheduled.",
            ephemeral=True
        )
        return

    await execute(
        "DELETE FROM announcements WHERE id=$1",
        (announcement_id,)
    )

    await interaction.response.send_message(
        "✅ Announcement unscheduled.",
        ephemeral=True
    )


# ─────────────────────────────────────────────────────────────
# Lists all announcements, including their content and status
# ─────────────────────────────────────────────────────────────
@app_commands.default_permissions(manage_messages=True)
@bot.tree.command(name="announcement_view")
async def announcement_view(interaction: discord.Interaction):
    rows = await fetchall(
        """
        SELECT id, title, send_at, end_at, state, content
        FROM announcements
        ORDER BY
        CASE state
            WHEN 'scheduled' THEN 1
            WHEN 'sent' THEN 2
            WHEN 'closed' THEN 3
            ELSE 4
        END,
        send_at ASC
        """
    )

    if not rows:
        await interaction.response.send_message("No announcements found.", ephemeral=True)
        return

    embeds = []
    current_embed = discord.Embed(
        title="📋 Announcement Registry",
        color=discord.Color.blue(),
        description="Showing all stored announcements and their content."
    )

    for aid, title, send_at, end_at, state, content in rows:
        # 1. Handle Field Limits (Discord limit is 25 per embed)
        # We use a lower limit (e.g., 8-10) because content previews make the embed very tall
        if len(current_embed.fields) >= 8: 
            embeds.append(current_embed)
            current_embed = discord.Embed(color=discord.Color.blue())

        # 2. Format Timestamp
        send_at_display = fmt_time(send_at)
        end_at_display = fmt_time(end_at) if end_at else "—"


        # 3. Format Content Preview
        # Truncate content to 200 chars to avoid hitting embed character limits
        preview = content if len(content) <= 200 else content[:197] + "..."
        
        status_emoji = {"scheduled": "⏳", "sent": "✅", "closed": "🔒"}.get(state, "❓")
        
        # 4. Add Field
        embed_value = (
            f"**ID:** `{aid}`\n"
            f"**Status:** {state.capitalize()}\n"
            f"**Send:** {send_at_display}\n"
            f"**End:** {end_at_display}\n"
            f"```text\n{preview}\n```"
        )

        current_embed.add_field(
            name=f"{status_emoji} {title}",
            value=embed_value,
            inline=False
        )

    embeds.append(current_embed)

    # Note: Discord supports up to 10 embeds per message.
    # We slice to 10 to prevent crashes in case DB is very large.
    await interaction.response.send_message(embeds=embeds[:10], ephemeral=True)


bot.run(os.getenv("DISCORD_TOKEN"))
