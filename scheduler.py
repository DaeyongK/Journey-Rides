import os
import asyncio
from db import fetchall, execute, fetchone
from time_utils import now, get_cutoff_iso, format_close_time
from views import RideView
from dashboard import render_dashboard, refresh_dashboard_for_announcement
from dashboard_paginator import DashboardPaginator
from dotenv import load_dotenv
load_dotenv()

PUBLIC_CHANNEL_ID = int(os.getenv("PUBLIC_CHANNEL_ID"))
ADMIN_CHANNEL_ID = int(os.getenv("ADMIN_CHANNEL_ID"))
REFRESH_INTERVAL = 1  # seconds


# ─────────────────────────────────────────────────────────────
# Main scheduler loop
# ─────────────────────────────────────────────────────────────
async def scheduler_loop(bot):
    await bot.wait_until_ready()
    print("[scheduler] started")

    while not bot.is_closed():
        try:
            sent_announcement_ids = await send_scheduled_announcements(bot)
            closed_announcement_ids = await close_expired_announcements(bot)
            for aid in sent_announcement_ids:
                await refresh_dashboard_for_announcement(bot, aid)
            for aid in closed_announcement_ids:
                await refresh_dashboard_for_announcement(bot, aid)
            await purge_old_announcements(bot)
        except Exception as e:
            print(f"[scheduler] error: {e}")

        await asyncio.sleep(REFRESH_INTERVAL)


# ─────────────────────────────────────────────────────────────
# Sends scheduled announcements:
# Returns list of sent announcement ids
# ─────────────────────────────────────────────────────────────
async def send_scheduled_announcements(bot) -> list:
    rows = await fetchall(
        """
        SELECT id, title, content, reactable, end_at
        FROM announcements
        WHERE state='scheduled'
          AND send_at <= ?
        """,
        (now(),)
    )

    if not rows:
        return []

    public_ch = bot.get_channel(PUBLIC_CHANNEL_ID)
    if not public_ch:
        try:
            public_ch = await bot.fetch_channel(PUBLIC_CHANNEL_ID)
        except Exception:
            return

    admin_ch = bot.get_channel(ADMIN_CHANNEL_ID)
    if not admin_ch:
        try:
            admin_ch = await bot.fetch_channel(ADMIN_CHANNEL_ID)
        except Exception:
            admin_ch = None

    sent_announcement_ids = []
    for announcement_id, title, content, reactable, end_at in rows:
        view = RideView(announcement_id, False) if reactable else None

        if reactable:
            header = f"**{title}**\n{format_close_time(end_at)}"
        else:
            header = f"**{title}**"

        message_text = f"{header}\n\n{content}"

        msg = await public_ch.send(message_text, view=view)

        dashboard_msg_id = None
        if reactable and admin_ch:
            dashboard_msg_id = await create_dashboard(
                bot=bot,
                announcement_id=announcement_id,
                title=title,
                end_at=end_at,
                admin_ch=admin_ch,
            )

        await execute(
            """
            UPDATE announcements
            SET state='sent',
                message_id=?,
                dashboard_message_id=?,
                dashboard_page=0
            WHERE id=?
            """,
            (msg.id, dashboard_msg_id, announcement_id)
        )
        sent_announcement_ids.append(announcement_id)
    return sent_announcement_ids

# ─────────────────────────────────────────────────────────────
# Creates initial admin dashboard
# ─────────────────────────────────────────────────────────────
async def create_dashboard(bot, announcement_id, title, end_at, admin_ch):
    embeds = await render_dashboard(
        bot=bot,
        announcement_id=announcement_id,
        title=title,
        end_at=end_at,
    )
    if not embeds:
        return None

    view = DashboardPaginator(
        embeds=embeds,
        announcement_id=announcement_id,
        title=title,
        start_index=0,
    )

    msg = await admin_ch.send(
        embed=view._current_embed(),
        view=view,
    )

    return msg.id


# ─────────────────────────────────────────────────────────────
# Closes expired announcements (state only)
# Returns list of closed announcements
# ─────────────────────────────────────────────────────────────
async def close_expired_announcements(bot) -> list:
    rows = await fetchall(
        """
        SELECT id, message_id, reactable
        FROM announcements
        WHERE state='sent'
          AND end_at IS NOT NULL
          AND end_at <= ?
        """,
        (now(),)
    )

    if not rows:
        return []

    public_ch = bot.get_channel(PUBLIC_CHANNEL_ID)
    if not public_ch:
        try:
            public_ch = await bot.fetch_channel(PUBLIC_CHANNEL_ID)
        except Exception:
            return []

    closed_announcement_ids = []

    for announcement_id, message_id, reactable in rows:
        await execute(
            "UPDATE announcements SET state='closed' WHERE id=?",
            (announcement_id,)
        )

        if reactable:
            try:
                msg = await public_ch.fetch_message(message_id)
                row = await fetchone(
                    "SELECT title, content FROM announcements WHERE id=?",
                    (announcement_id,)
                )
                if row:
                    title, content = row
                    new_text = (
                        f"**{title}**\n"
                        "🔒 Requests are now closed for this announcement.\n\n"
                        f"{content}"
                    )
                    await msg.edit(
                        content=new_text,
                        view=RideView(announcement_id, is_closed=True)
                    )
            except Exception:
                pass


        closed_announcement_ids.append(announcement_id)

    return closed_announcement_ids


# ─────────────────────────────────────────────────────────────
# Permanently deletes all expired announcements older than 180 days
# Deletes from database and Discord channels
# ─────────────────────────────────────────────────────────────
async def purge_old_announcements(bot):
    rows = await fetchall(
        """
        SELECT id
        FROM announcements
        WHERE end_at IS NOT NULL
          AND end_at <= ?
        """,
        (get_cutoff_iso(days=180),)
    )
    for (announcement_id,) in rows:
        success = await delete_announcement(bot, announcement_id)
        if success:
            print(f"[scheduler] purged announcement {announcement_id}")


# ─────────────────────────────────────────────────────────────
# Permanently deletes an announcement
# Deletes from database and Discord channels
# ─────────────────────────────────────────────────────────────
async def delete_announcement(bot, announcement_id: str) -> bool:
    row = await fetchone(
        """
        SELECT message_id, dashboard_message_id
        FROM announcements
        WHERE id=?
        """,
        (announcement_id,)
    )

    if not row:
        return False

    message_id, dashboard_msg_id = row

    # ──────────────── Delete public message ────────────────
    if message_id:
        public_ch = bot.get_channel(PUBLIC_CHANNEL_ID)
        if not public_ch:
            try:
                public_ch = await bot.fetch_channel(PUBLIC_CHANNEL_ID)
            except Exception:
                public_ch = None

        if public_ch:
            try:
                msg = await public_ch.fetch_message(message_id)
                await msg.delete()
            except Exception:
                pass

    # ──────────────── Delete admin dashboard ────────────────
    if dashboard_msg_id:
        admin_ch = bot.get_channel(ADMIN_CHANNEL_ID)
        if not admin_ch:
            try:
                admin_ch = await bot.fetch_channel(ADMIN_CHANNEL_ID)
            except Exception:
                admin_ch = None

        if admin_ch:
            try:
                dash_msg = await admin_ch.fetch_message(dashboard_msg_id)
                await dash_msg.delete()
            except Exception:
                pass

    # ──────────────── Delete DB row ────────────────
    await execute(
        "DELETE FROM announcements WHERE id=?",
        (announcement_id,)
    )

    # ──────────────── Delete associated ride entries ────────────────
    await execute(
        "DELETE FROM ride_entries WHERE announcement_id=?",
        (announcement_id,)
    )

    return True
