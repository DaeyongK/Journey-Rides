import os
import uuid
from datetime import date as _date
import discord
from discord import app_commands
from discord.ext import commands
from db import init_db, execute, fetchall, fetchone
from time_utils import parse_to_utc_iso, fmt_time, month_ride_dates, parse_month, ride_type_label, ride_type_for_date
from views import AnnouncementContentModal, AnnouncementEditModal, RideView, get_school
from dashboard import render_dashboard
from dashboard_paginator import DashboardPaginator
from scheduler import scheduler_loop, delete_announcement
import availability
from availability_views import AvailabilityView
from dotenv import load_dotenv
load_dotenv()

PUBLIC_CHANNEL_ID = int(os.getenv("PUBLIC_CHANNEL_ID"))
ADMIN_CHANNEL_ID = int(os.getenv("ADMIN_CHANNEL_ID"))
ALLOWED_ROLE_ID = int(os.getenv("ALLOWED_ROLE_ID"))

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
        for poll_id, school, state in await availability.list_restorable_poll_messages():
            occurrences = await availability.get_occurrences(poll_id, school)
            bot.add_view(
                AvailabilityView(
                    poll_id,
                    school,
                    occurrences,
                    is_closed=(state == "closed"),
                )
            )

        # Post/refresh the admin availability display for active polls
        for poll in await availability.list_restorable_polls():
            await availability.post_or_refresh_admin_availability_message(bot, poll["id"])

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
    reactable="True = signup buttons + admin dashboard. False = plain announcement with no buttons.",
    ride_date="Optional 'YYYY-MM-DD'. A Friday or Sunday matching the category. Auto-registers scheduled drivers.",
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
        if ride_date:
            embed_value += f"**Ride date:** {ride_date} (driver pipeline on)\n"

        current_embed.add_field(
            name=f"{status_emoji} {title}",
            value=embed_value,
            inline=False
        )

    embeds.append(current_embed)

    await interaction.response.send_message(embeds=embeds[:1], ephemeral=True)


# ═════════════════════════════════════════════════════════════
# Monthly Driver Availability & Assignment
# ═════════════════════════════════════════════════════════════
RIDE_TYPE_CHOICES = [
    app_commands.Choice(name="Friday PM", value="F"),
    app_commands.Choice(name="Sunday Service", value="S"),
]


async def _resolve_channel(channel_id):
    if not channel_id:
        return None
    ch = bot.get_channel(channel_id)
    if ch is None:
        try:
            ch = await bot.fetch_channel(channel_id)
        except Exception:
            ch = None
    return ch


# ─────────────────────────────────────────────────────────────
# Opens a monthly availability poll and posts a driver-facing
# dropdown into each school's availability channel.
# ─────────────────────────────────────────────────────────────
@app_commands.default_permissions(manage_messages=True)
@bot.tree.command(
    name="availability_create",
    description="Open a monthly driver-availability poll (a dropdown in each school's channel). Once per month.",
)
@app_commands.describe(
    month="Month to collect for. 'YYYY-MM', e.g. 2026-09. Every Friday + Sunday that month becomes an option.",
    exclude="Optional dates to skip (holidays). Comma-separated 'YYYY-MM-DD', e.g. 2026-09-25,2026-09-27",
    sunday_host="Optional per-Sunday host: e.g. 2026-09-06J, 2026-09-13G. J=joint, E=Emory service, G=GT service.",
)
async def availability_create(
    interaction: discord.Interaction,
    month: str,
    exclude: str = "",
    sunday_host: str = "",
):
    try:
        parse_month(month)
    except ValueError:
        await interaction.response.send_message(
            "❌ Invalid `month`. Use exactly `YYYY-MM` (e.g. 2026-09).", ephemeral=True
        )
        return

    existing = await availability.get_poll_by_month(month)
    if existing:
        await interaction.response.send_message(
            f"❌ An availability check has already been run for `{month}` (state: {existing['state']}). "
            "A month can only be sent once. Use `/availability_view` to see it, or `/availability_assign` to (re)build the schedule.",
            ephemeral=True,
        )
        return

    excluded = set()
    for token in (t.strip() for t in exclude.split(",")):
        if not token:
            continue
        try:
            excluded.add(_date.fromisoformat(token))
        except ValueError:
            await interaction.response.send_message(
                f"❌ Invalid date in `exclude`: `{token}`. Use `YYYY-MM-DD`.", ephemeral=True
            )
            return

    occurrences = [o for o in month_ride_dates(month) if o[0] not in excluded]
    if not occurrences:
        await interaction.response.send_message(
            "❌ No ride dates left after exclusions.", ephemeral=True
        )
        return

    # Per-Sunday host campus -> which schools' drivers are needed.
    sunday_hosts = {}
    if sunday_host.strip():
        try:
            sunday_hosts = availability.parse_sunday_hosts(sunday_host)
        except ValueError as e:
            await interaction.response.send_message(
                f"❌ `sunday_host`: {e}", ephemeral=True
            )
            return
        month_sundays = {d for d, t in occurrences if t == "S"}
        missing = month_sundays - set(sunday_hosts)
        extra = set(sunday_hosts) - month_sundays
        if missing or extra:
            parts = []
            if missing:
                parts.append(
                    "not listed: " + ", ".join(d.isoformat() for d in sorted(missing))
                )
            if extra:
                parts.append(
                    "not a Sunday this month (or excluded): "
                    + ", ".join(d.isoformat() for d in sorted(extra))
                )
            await interaction.response.send_message(
                "❌ `sunday_host` must list every Sunday service this month, "
                "exactly once — " + "; ".join(parts),
                ephemeral=True,
            )
            return

    # (date, ride_type, [schools]) — Fridays and unmapped Sundays go to all schools
    occ_rows = [
        (d, t, availability.schools_for_occurrence(d, t, sunday_hosts))
        for d, t in occurrences
    ]

    # Resolve each school's channel up front
    channels = {}
    for school in availability.SCHOOLS:
        ch = await _resolve_channel(availability.AVAILABILITY_CHANNELS.get(school))
        if ch is not None:
            channels[school] = ch

    if not channels:
        await interaction.response.send_message(
            "❌ No availability channels configured. Set `AVAILABILITY_CHANNEL_ID_GT` "
            "and `AVAILABILITY_CHANNEL_ID_EMORY`.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)

    poll_id = await availability.create_poll(month)
    await availability.add_occurrences(poll_id, occ_rows)

    posted = []
    for school, channel in channels.items():
        school_occ = [(d, t) for d, t, sch in occ_rows if school in sch]
        date_lines = "\n".join(
            f"• {availability.occurrence_label(d, t)}" for d, t in school_occ
        )
        view = AvailabilityView(poll_id, school, school_occ, is_closed=False)
        bot.add_view(view)
        msg = await channel.send(
            content=(
                f"**🗓 {school} Driver Availability — {month}**\n"
                "Select **every** date you're able to drive this month. "
                "Re-selecting replaces your previous answer.\n\n"
                f"{date_lines}"
            ),
            view=view,
        )
        await availability.add_poll_message(poll_id, school, channel.id, msg.id)
        posted.append(f"{school} → {channel.mention}")

    # Live availability display in the admin channel
    await availability.post_or_refresh_admin_availability_message(bot, poll_id)

    missing = [s for s in availability.SCHOOLS if s not in channels]
    note = f"\n⚠️ No channel for: {', '.join(missing)}" if missing else ""

    host_note = ""
    if sunday_hosts:
        rows = []
        for d in sorted(sunday_hosts):
            sch = sunday_hosts[d]
            who = ", ".join(sch) + ("" if len(sch) > 1 else " only")
            rows.append(f"• {availability.occurrence_label(d, 'S')} → **{who}**")
        host_note = "\n\n**Sunday services — drivers needed from:**\n" + "\n".join(rows)

    await interaction.followup.send(
        f"✅ Availability poll created for `{month}` (`{poll_id}`).\n"
        + "\n".join(posted)
        + "\nA live availability display was posted to the admin channel."
        + host_note
        + note,
        ephemeral=True,
    )


# ─────────────────────────────────────────────────────────────
# Shows the current availability grid (before assigning)
# ─────────────────────────────────────────────────────────────
@app_commands.default_permissions(manage_messages=True)
@bot.tree.command(
    name="availability_view",
    description="Show a month's collected availability (by ride and by driver), ephemeral.",
)
@app_commands.describe(month="Month to view. Format 'YYYY-MM', e.g. 2026-09.")
async def availability_view(interaction: discord.Interaction, month: str):
    poll = await availability.get_poll_by_month(month)
    if not poll:
        await interaction.response.send_message(
            f"❌ No availability poll found for `{month}`.", ephemeral=True
        )
        return

    embeds = await availability.render_availability_embeds(bot, poll["id"])
    await interaction.response.send_message(embeds=embeds, ephemeral=True)


# ─────────────────────────────────────────────────────────────
# Auto-assigns drivers toward each ride's base target (even load) and posts
# the schedule to the admin channel. Full recompute — clears manual edits.
# ─────────────────────────────────────────────────────────────
@app_commands.default_permissions(manage_messages=True)
@bot.tree.command(
    name="availability_assign",
    description="Auto-assign drivers for a month, post the schedule, and sync any sent announcements.",
)
@app_commands.describe(month="Month to assign. 'YYYY-MM', e.g. 2026-09. The poll must already exist.")
async def availability_assign(interaction: discord.Interaction, month: str):
    poll = await availability.get_poll_by_month(month, active_only=True)
    if not poll:
        await interaction.response.send_message(
            f"❌ No open availability poll found for `{month}`.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    shortfalls = await availability.auto_assign(poll["id"])
    await availability.set_poll_state(poll["id"], "assigned")
    await availability.post_or_refresh_admin_message(bot, poll["id"])

    # Push the new assignments into any already-sent announcements for these rides
    synced = 0
    for ride_date, ride_type in await availability.get_occurrences(poll["id"]):
        synced += await availability.sync_assignments_to_announcements(bot, ride_date, ride_type)

    if shortfalls:
        lines = "\n".join(
            f"• {school} — {availability.occurrence_label(d, t)} ({got}/{target})"
            for school, d, t, got, target in shortfalls
        )
        note = (
            "\n\n**Below the driver target** — not enough availability submitted. "
            "No call-out is posted now; if a ride actually closes short on seats "
            f"vs riders, drivers are requested then.\n{lines}"
        )
    else:
        note = "\n\nEvery ride is at its driver target. ✅"

    if synced:
        note += f"\n\n🔗 Updated **{synced}** already-sent announcement(s) with the new assignments."

    await interaction.followup.send(
        f"✅ Auto-assigned drivers for `{month}` and posted the schedule to the admin channel."
        f"{note}\n\n*Manual edits were cleared — use `/availability_adjust` to layer them back on.*",
        ephemeral=True,
    )


# ─────────────────────────────────────────────────────────────
# Adds or removes a single driver from one ride occurrence
# ─────────────────────────────────────────────────────────────
@app_commands.default_permissions(manage_messages=True)
@bot.tree.command(
    name="availability_adjust",
    description="Add or remove one driver for a single ride in an assigned month's schedule.",
)
@app_commands.describe(
    month="Month of the schedule. Format 'YYYY-MM', e.g. 2026-09.",
    date="The ride's date. Format 'YYYY-MM-DD', e.g. 2026-09-06. Must be one of that poll's ride dates.",
    ride_type="Friday PM or Sunday Service (pick from the list).",
    driver="The member to add or remove. Must have a GT or Emory role.",
    action="add = put this driver on the ride; remove = take them off.",
)
@app_commands.choices(
    ride_type=RIDE_TYPE_CHOICES,
    action=[
        app_commands.Choice(name="add", value="add"),
        app_commands.Choice(name="remove", value="remove"),
    ],
)
async def availability_adjust(
    interaction: discord.Interaction,
    month: str,
    date: str,
    ride_type: app_commands.Choice[str],
    driver: discord.Member,
    action: app_commands.Choice[str],
):
    poll = await availability.get_poll_by_month(month)
    if not poll:
        await interaction.response.send_message(
            f"❌ No availability poll found for `{month}`.", ephemeral=True
        )
        return

    try:
        ride_date = _date.fromisoformat(date)
    except ValueError:
        await interaction.response.send_message(
            "❌ Invalid `date`. Use `YYYY-MM-DD`.", ephemeral=True
        )
        return

    if not await availability.occurrence_exists(poll["id"], ride_date, ride_type.value):
        await interaction.response.send_message(
            f"❌ `{date}` ({ride_type_label(ride_type.value)}) is not a ride date in this poll.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)

    if action.value == "add":
        school = get_school(driver)
        if school not in availability.SCHOOLS:
            await interaction.followup.send(
                f"❌ {driver.display_name} isn't in a school that uses the availability "
                f"system ({', '.join(availability.SCHOOLS)}).",
                ephemeral=True,
            )
            return
        await availability.add_assignment(
            poll["id"], driver.id, ride_date, ride_type.value, school,
            assigned_by=interaction.user.display_name,
        )
        verb = "Added"
    else:
        removed = await availability.remove_assignment(
            poll["id"], driver.id, ride_date, ride_type.value
        )
        if not removed:
            await interaction.followup.send(
                f"ℹ️ {driver.display_name} was not assigned to that ride.", ephemeral=True
            )
            return
        verb = "Removed"

    await availability.refresh_admin_message(bot, poll["id"])

    # Push the change into any already-sent announcement for this ride
    synced = await availability.sync_assignments_to_announcements(
        bot, ride_date, ride_type.value
    )
    tail = (
        f" Updated {synced} already-sent announcement(s)." if synced else ""
    )
    await interaction.followup.send(
        f"✅ {verb} {driver.display_name} — {availability.occurrence_label(ride_date, ride_type.value)}.{tail}",
        ephemeral=True,
    )


# ─────────────────────────────────────────────────────────────
# Closes an availability poll and disables the dropdown
# ─────────────────────────────────────────────────────────────
@app_commands.default_permissions(manage_messages=True)
@bot.tree.command(
    name="availability_close",
    description="Close a month's availability poll and disable its dropdowns.",
)
@app_commands.describe(month="Month to close. Format 'YYYY-MM', e.g. 2026-09.")
async def availability_close(interaction: discord.Interaction, month: str):
    poll = await availability.get_poll_by_month(month, active_only=True)
    if not poll:
        await interaction.response.send_message(
            f"❌ No open availability poll found for `{month}`.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    await availability.set_poll_state(poll["id"], "closed")

    for school, channel_id, message_id in await availability.get_poll_messages(poll["id"]):
        occurrences = await availability.get_occurrences(poll["id"], school)
        closed_view = AvailabilityView(poll["id"], school, occurrences, is_closed=True)
        bot.add_view(closed_view)
        channel = await _resolve_channel(channel_id)
        if not channel:
            continue
        try:
            msg = await channel.fetch_message(message_id)
            await msg.edit(
                content=(
                    f"**🗓 {school} Driver Availability — {month}**\n"
                    "🔒 This availability poll is closed."
                ),
                view=closed_view,
            )
        except Exception:
            pass

    await interaction.followup.send(f"✅ Closed the availability poll for `{month}`.", ephemeral=True)


bot.run(os.getenv("DISCORD_TOKEN"))
