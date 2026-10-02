import os
import uuid
from datetime import date as _date, timedelta
import discord
from discord import app_commands
from discord.ext import commands
from db import init_db, execute, fetchall, fetchone
from time_utils import parse_to_utc_iso, fmt_time, ride_type_label, ride_type_for_date, combine_eastern_to_utc, fmt_ride_date
from views import AnnouncementContentModal, AnnouncementEditModal, RideView
from dashboard import render_dashboard
from dashboard_paginator import DashboardPaginator
from scheduler import scheduler_loop, delete_announcement
import availability
from dotenv import load_dotenv
from service_templates import SERVICE_TEMPLATES
load_dotenv()

PUBLIC_CHANNEL_ID = int(os.getenv("PUBLIC_CHANNEL_ID"))
ADMIN_CHANNEL_ID = int(os.getenv("ADMIN_CHANNEL_ID"))
ALLOWED_ROLE_ID = int(os.getenv("ALLOWED_ROLE_ID"))

SERVICE_TYPE_CHOICES = [
    app_commands.Choice(name="Friday Prayer Room", value="F"),
    app_commands.Choice(name="Sunday Joint Service", value="SJ"),
    app_commands.Choice(name="Sunday College Service", value="SC"),
]

intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
bot.setup = False

@bot.event
async def on_ready():
    if not bot.setup:
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

        # Restoring per-school availability dropdowns (persistent select menus)
        await availability.restore_views(bot)

        await bot.add_cog(availability.AvailabilityCommands(bot))

        # Sync commands
        guild = discord.Object(id=int(os.getenv("SERVER_ID")))
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)

        # Start scheduler loop
        bot.loop.create_task(scheduler_loop(bot))
        bot.setup = True
    else:
        print("Bot is already set up and ready.")


# ─────────────────────────────────────────────────────────────
# Creates a scheduled announcement
# Format time like 'YYYY-MM-DD HH:MM' in US/Eastern.
# ─────────────────────────────────────────────────────────────
@app_commands.default_permissions(manage_messages=True)
@bot.tree.command(
    name="announcement_create",
    description="Schedule an announcement to auto-post, and optionally auto-close signups, at set times.",
)
@app_commands.describe(
    title="Bold heading on the post and dashboard. Free text, e.g. 'Sunday Service Rides for 1/11/2026'.",
    send_at="When it posts. 'YYYY-MM-DD HH:MM', US/Eastern 24h, e.g. 2026-01-04 08:00.",
    end_at="When signups close. Same format as send_at; must be >= send_at. Non-reactable: any future time.",
    reactable="True = signup buttons + admin dashboard (pick category F/S/E in the modal). False = plain announcement.",
    ride_date="Optional 'YYYY-MM-DD'. A Friday or Sunday matching the category (F/S only). Auto-registers scheduled drivers.",
)
async def create(
    interaction: discord.Interaction,
    title: str,
    send_at: str,
    end_at: str,
    reactable: bool,
    ride_date: str = "",
):
    aid = str(uuid.uuid4())

    ride_date_val = None
    if ride_date:
        try:
            ride_date_val = _date.fromisoformat(ride_date)
        except ValueError:
            await interaction.response.send_message(
                "Invalid `ride_date` format. Use exactly: 'YYYY-MM-DD' (e.g. 2026-09-06).",
                ephemeral=True,
            )
            return

        if ride_type_for_date(ride_date_val) is None:
            await interaction.response.send_message(
                f"`ride_date` {ride_date} is a "
                f"{ride_date_val.strftime('%A')} — it must fall on a Friday (Friday PM) "
                "or a Sunday (Sunday Service).",
                ephemeral=True,
            )
            return

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
            ride_date=ride_date_val,
        )
    )


# ─────────────────────────────────────────────────────────────
# Edits a sent announcement
# ─────────────────────────────────────────────────────────────
@app_commands.default_permissions(manage_messages=True)
@bot.tree.command(
    name="announcement_edit",
    description="Edit the title/body (and ride category) of an already-sent or closed announcement.",
)
@app_commands.describe(
    announcement_id="UUID from /announcement_view, e.g. 550e8400-e29b-41d4-a716-446655440000. Must be sent or closed.",
)
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
        SELECT title, content, state, content_category
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

    title, content, state, content_category = row

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
            old_content_category=content_category
        )
    )

# ─────────────────────────────────────────────────────────────
# Deletes an already-posted announcement
# ─────────────────────────────────────────────────────────────
@app_commands.default_permissions(manage_messages=True)
@bot.tree.command(
    name="announcement_delete",
    description="Permanently delete a sent/closed announcement, its dashboard and all its signups.",
)
@app_commands.describe(
    announcement_id="UUID from /announcement_view. Permanently removes post + dashboard + signups. Sent/closed only.",
)
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
@bot.tree.command(
    name="announcement_unschedule",
    description="Cancel a still-scheduled announcement before it posts.",
)
@app_commands.describe(
    announcement_id="UUID from /announcement_view. Only works while still 'scheduled'; deletes the pending announcement.",
)
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
@bot.tree.command(
    name="announcement_view",
    description="List every announcement with its ID, status, send/end times and ride date.",
)
async def announcement_view(interaction: discord.Interaction):
    rows = await fetchall(
        """
        SELECT id, title, send_at, end_at, state, content, content_category, reactable, ride_date
        FROM announcements
        ORDER BY end_at DESC NULLS LAST
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

    for aid, title, send_at, end_at, state, content, content_category, reactable, ride_date in rows:
        # Handle Field Limits (Discord limit is 25 per embed)
        if len(current_embed.fields) >= 6:
            embeds.append(current_embed)
            current_embed = discord.Embed(color=discord.Color.blue())

        # Format Timestamp
        send_at_display = fmt_time(send_at)
        end_at_display = fmt_time(end_at) if end_at else "—"

        status_emoji = {"scheduled": "⏳", "sent": "✅", "closed": "🔒"}.get(state, "❓")

        # Add Field
        embed_value = (
            f"**ID:** `{aid}`\n"
            f"**Status:** {state.capitalize()}\n"
            f"**Send:** {send_at_display}\n"
            f"**End:** {end_at_display}\n"
        )
        if reactable and content_category:
            embed_value += f"**Category:** {ride_type_label(content_category)}\n"
        if ride_date:
            embed_value += f"**Ride date:** {ride_date} (driver pipeline on)\n"

        current_embed.add_field(
            name=f"{status_emoji} {title}",
            value=embed_value,
            inline=False
        )

    embeds.append(current_embed)

    await interaction.response.send_message(embeds=embeds[:1], ephemeral=True)


@app_commands.default_permissions(manage_messages=True)
@bot.tree.command(
    name="announcement_service",
    description="Auto-create the 3 linked weekly ride announcements (open/reminder/recap) from one date + type.",
)
@app_commands.describe(
    ride_date="The Friday or Sunday this is for. 'YYYY-MM-DD', e.g. 2026-09-04.",
    type="F = Friday Prayer Room, SJ = Sunday Joint Service, SC = Sunday College Service.",
    onsite_poc="Name and phone number in one field, e.g. 'Jane Doe, 404-555-1234'.",
)
@app_commands.choices(type=SERVICE_TYPE_CHOICES)
async def announcement_service(
    interaction: discord.Interaction,
    ride_date: str,
    type: app_commands.Choice[str],
    onsite_poc: str,
):
    tpl = SERVICE_TEMPLATES[type.value]

    try:
        rd = _date.fromisoformat(ride_date)
    except ValueError:
        await interaction.response.send_message(
            "❌ Invalid `ride_date`. Use exactly `YYYY-MM-DD` (e.g. 2026-09-04).", ephemeral=True
        )
        return

    if ride_type_for_date(rd) != tpl["ride_weekday"]:
        needed = "Friday" if tpl["ride_weekday"] == "F" else "Sunday"
        await interaction.response.send_message(
            f"❌ `{ride_date}` is a {rd.strftime('%A')}, but **{tpl['label']}** must fall on a {needed}.",
            ephemeral=True,
        )
        return

    placeholders = {"date": fmt_ride_date(rd), "poc": onsite_poc}

    created = []
    for slot_key in ("open", "reminder", "recap"):
        slot = tpl["slots"][slot_key]
        send_at_dt = combine_eastern_to_utc(rd + timedelta(days=slot["send_offset_days"]), slot["send_time"])
        end_at_dt = combine_eastern_to_utc(rd + timedelta(days=slot["end_offset_days"]), slot["end_time"])

        aid = str(uuid.uuid4())
        title = slot["title"].format(**placeholders)
        body = slot["body"].format(**placeholders)

        await execute(
            """
            INSERT INTO announcements
                (id, title, content, content_category, send_at, end_at, state, reactable, ride_date)
            VALUES ($1, $2, $3, $4, $5, $6, 'scheduled', $7, $8)
            """,
            (aid, title, body, tpl["content_category"], send_at_dt, end_at_dt, slot["reactable"], rd),
        )
        created.append((slot_key, aid, send_at_dt))

    lines = "\n".join(
        f"• **{slot_key}** — `{aid}` — sends {fmt_time(send_at)}"
        for slot_key, aid, send_at in created
    )
    await interaction.response.send_message(
        f"✅ Created **{tpl['label']}** series for `{ride_date}`:\n{lines}",
        ephemeral=True,
    )

bot.run(os.getenv("DISCORD_TOKEN"))
