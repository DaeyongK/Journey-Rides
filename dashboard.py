import os
import discord
from db import fetchone, fetchall
from time_utils import format_close_time
from dashboard_paginator import DashboardPaginator
from dotenv import load_dotenv
load_dotenv()

SCHOOLS = ["GT", "Emory", "GSU"]
ADMIN_CHANNEL_ID = int(os.getenv("ADMIN_CHANNEL_ID"))
SERVER_ID = int(os.getenv("SERVER_ID"))

# ─────────────────────────────────────────────────────────────
# Dashboard Rendering:
# Creates the contents of the admin dashboard
# Navigation controls are defined in views.py
# ─────────────────────────────────────────────────────────────
async def render_dashboard(bot, announcement_id, title, end_at) -> list:
    # Aggregating Data
    rows = await fetchall(
        """
        SELECT user_id, school, role, seats
        FROM ride_entries
        WHERE announcement_id=$1
        ORDER BY school
        """,
        (announcement_id,)
    )

    data = {s: {"drivers": [], "riders": []} for s in SCHOOLS}
    guild = bot.get_guild(SERVER_ID)

    for user_id, school, role, seats in rows:
        if school not in data:
            continue

        member = None
        if guild:
            member = guild.get_member(user_id)
            if member is None:
                try:
                    member = await guild.fetch_member(user_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    member = None

        if not member:
            continue

        name = f"{member.display_name}"
        if role == "driver":
            data[school]["drivers"].append((name, seats))
        else:
            data[school]["riders"].append(name)

    # Creating First Page Cover (1/4)
    cover = discord.Embed(
        title=title,
        description=(format_close_time(end_at)),
        color=discord.Color.blue()
    )

    for school in SCHOOLS:
        drivers = data[school]["drivers"]
        riders = data[school]["riders"]

        seat_total = sum(seats for _, seats in drivers)
        rider_count = len(riders)
        status = "✅" if seat_total >= rider_count else "❌"

        cover.add_field(
            name=f"🏫 {school}",
            value=(
                f"Drivers: **{len(drivers)}**\n"
                f"Riders: **{rider_count}**\n"
                f"Seats: **{seat_total}** {status}"
            ),
            inline=False
        )

    embeds = [cover]

    # Creating School-Based Page Covers (2/4, 3/4, 4/4)
    for school in SCHOOLS:
        drivers = data[school]["drivers"]
        riders = data[school]["riders"]

        driver_lines = (
            "\n".join(f"🚗 {name} — {seats} seats" for name, seats in drivers)
            if drivers else "*None*"
        )

        rider_lines = (
            "\n".join(f"🙋 {name}" for name in riders)
            if riders else "*None*"
        )

        seat_total = sum(seats for _, seats in drivers)
        rider_count = len(riders)
        status = "✅" if seat_total >= rider_count else "❌"

        embeds.append(
            discord.Embed(
                title=f"🏫 {school} — Ride Signups",
                description=(
                    f"**Drivers**\n{driver_lines}\n\n"
                    f"**Riders**\n{rider_lines}\n\n"
                    f"**Summary**\n"
                    f"Seats: **{seat_total}** | Riders: **{rider_count}** {status}"
                ),
                color=discord.Color.blue()
            )
        )

    return embeds


# ─────────────────────────────────────────────────────────────
# Refreshes admin dashboard for a specific announcement 
# ─────────────────────────────────────────────────────────────
async def refresh_dashboard_for_announcement(bot, announcement_id):
    row = await fetchone(
        """
        SELECT dashboard_message_id, dashboard_page, title, end_at
        FROM announcements
        WHERE id=$1
        """,
        (announcement_id,)
    )
    if not row:
        return

    dash_msg_id, page_num, title, end_at = row
    if not dash_msg_id:
        return

    admin_ch = bot.get_channel(ADMIN_CHANNEL_ID)
    if not admin_ch:
        try:
            admin_ch = await bot.fetch_channel(ADMIN_CHANNEL_ID)
        except Exception:
            return

    try:
        dash_msg = await admin_ch.fetch_message(dash_msg_id)
    except Exception:
        return

    embeds = await render_dashboard(
        bot=bot,
        announcement_id=announcement_id,
        title=title,
        end_at=end_at,
    )
    if not embeds:
        return

    page = min(page_num or 0, len(embeds) - 1)

    view = DashboardPaginator(
        embeds=embeds,
        announcement_id=announcement_id,
        start_index=page,
        title=title,
    )

    await dash_msg.edit(
        embed=view._current_embed(),
        view=view,
    )
