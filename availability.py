import os
import uuid
from collections import defaultdict
from datetime import date as _date, datetime

import discord
from discord import app_commands
from discord.ext import commands

from dashboard import refresh_dashboard_for_announcement
from db import execute, executemany, fetchall, fetchone, row_lock
from exporter import remove_from_sheets, sync_to_sheets
from time_utils import fmt_ride_date, month_ride_dates, parse_month, ride_type_label
from views import get_school
from dotenv import load_dotenv
load_dotenv()

# ─────────────────────────────────────────────────────────────
# Settings
# ─────────────────────────────────────────────────────────────
# Schools that use the monthly availability / assignment system.
SCHOOLS = ["GT", "Emory"]
ADMIN_CHANNEL_ID = int(os.getenv("ADMIN_CHANNEL_ID"))
SERVER_ID = int(os.getenv("SERVER_ID"))


def _school_channel_id(school):
    """Channel for a school's availability dropdown, from
    AVAILABILITY_CHANNEL_ID_<SCHOOL>."""
    v = os.getenv(f"AVAILABILITY_CHANNEL_ID_{school.upper()}")
    return int(v) if v else None


# school -> channel id (None if not configured)
AVAILABILITY_CHANNELS = {s: _school_channel_id(s) for s in SCHOOLS}

# Base number of drivers auto-assignment aims to place on each ride, keyed by
# (school, ride_type) where ride_type is 'F' (Friday PM) or 'S' (Sunday Service).
# Anything not listed falls back to DEFAULT_ASSIGN_TARGET. These are only a goal —
# if fewer drivers are available the ride is filled as far as it can be and
# flagged as short.
DEFAULT_ASSIGN_TARGET = 1
ASSIGN_TARGETS = {
    ("GT", "S"): 10,
    ("GT", "F"): 5,
    ("Emory", "S"): 6,
    ("Emory", "F"): 3,
}

# Sunday-service host code -> which schools' drivers are needed. A service held on
# one campus needs the *other* campus's drivers to bring their students over.
HOST_CODE_SCHOOLS = {
    "J": ["GT", "Emory"],   # joint service — drivers from both
    "E": ["GT"],            # Emory-hosted service — GT drivers bring GT students
    "G": ["Emory"],         # GT-hosted service — Emory drivers bring Emory students
}

# Per-school embed identity (emoji + colour)
SCHOOL_STYLE = {
    "GT":    ("🐝", 0xB3A369),   # Georgia Tech gold
    "Emory": ("🦅", 0x012169),   # Emory blue
}

# Discord allows at most 25 options in a single select menu. A month only ever
# has ~8-10 ride occurrences, so one select is always enough; this is a guard.
MAX_OPTIONS = 25


# ─────────────────────────────────────────────────────────────
# Small helpers
# ─────────────────────────────────────────────────────────────
def assign_target(school, ride_type) -> int:
    return ASSIGN_TARGETS.get((school, ride_type), DEFAULT_ASSIGN_TARGET)


def parse_sunday_hosts(raw: str) -> dict:
    """Parse '2026-09-06J, 2026-09-13G, ...' into {date: [schools]}.

    Each token is an ISO date followed by a single host code (J/E/G, see
    HOST_CODE_SCHOOLS). Whitespace, newlines and a trailing comma are ignored.
    Raises ValueError with a user-facing message on bad input.
    """
    out = {}
    for token in raw.replace("\n", ",").split(","):
        token = token.strip()
        if not token:
            continue
        code = token[-1].upper()
        if code not in HOST_CODE_SCHOOLS:
            raise ValueError(
                f"`{token}` must end in **J** (joint), **E** (Emory service) or "
                "**G** (GT service)."
            )
        try:
            d = _date.fromisoformat(token[:-1].strip())
        except ValueError:
            raise ValueError(
                f"`{token}` — put a `YYYY-MM-DD` date before the letter."
            )
        if d in out:
            raise ValueError(f"`{d.isoformat()}` is listed more than once.")
        out[d] = list(HOST_CODE_SCHOOLS[code])
    return out


def schools_for_occurrence(ride_date, ride_type, sunday_hosts: dict):
    """Which schools an occurrence belongs to. Sundays present in `sunday_hosts`
    use that mapping; everything else (Fridays, unmapped Sundays) is all schools."""
    if ride_type == "S" and ride_date in sunday_hosts:
        return list(sunday_hosts[ride_date])
    return list(SCHOOLS)


# Select-menu option values: "YYYY-MM-DD|F"  <->  (date, "F")
def encode_occurrence(ride_date, ride_type: str) -> str:
    return f"{ride_date.isoformat()}|{ride_type}"


def decode_occurrence(value: str):
    iso, ride_type = value.split("|", 1)
    return _date.fromisoformat(iso), ride_type


def occurrence_label(ride_date, ride_type: str) -> str:
    return f"{fmt_ride_date(ride_date)} · {ride_type_label(ride_type)}"


def _month_title(month: str) -> str:
    """'2026-09' -> 'September 2026'."""
    try:
        return datetime.strptime(month, "%Y-%m").strftime("%B %Y")
    except ValueError:
        return month


def _trunc(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


async def _channel(bot, channel_id):
    if not channel_id:
        return None
    ch = bot.get_channel(channel_id)
    if ch is None:
        try:
            ch = await bot.fetch_channel(channel_id)
        except Exception:
            ch = None
    return ch


async def _resolve_member(guild, user_id):
    if not guild:
        return None
    member = guild.get_member(user_id)
    if member is None:
        try:
            member = await guild.fetch_member(user_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            member = None
    return member


async def _display_names(bot, user_ids):
    guild = bot.get_guild(SERVER_ID)
    names = {}
    for user_id in set(user_ids):
        member = await _resolve_member(guild, user_id)
        names[user_id] = member.display_name if member else f"Unknown ({user_id})"
    return names


async def _send_admin_note(bot, lines):
    admin_ch = await _channel(bot, ADMIN_CHANNEL_ID)
    if admin_ch:
        try:
            await admin_ch.send("\n".join(lines))
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────
# DB — polls
# ─────────────────────────────────────────────────────────────
_POLL_COLUMNS = "id, month, state, admin_message_id"


async def create_poll(month: str) -> str:
    poll_id = str(uuid.uuid4())
    await execute(
        "INSERT INTO availability_polls (id, month) VALUES ($1, $2)",
        (poll_id, month),
    )
    return poll_id


async def get_poll_by_month(month: str, active_only: bool = False):
    query = f"SELECT {_POLL_COLUMNS} FROM availability_polls WHERE month=$1"
    if active_only:
        query += " AND state <> 'closed'"
    query += " ORDER BY created_at DESC"
    return await fetchone(query, (month,))


async def get_poll(poll_id):
    return await fetchone(
        f"SELECT {_POLL_COLUMNS} FROM availability_polls WHERE id=$1",
        (poll_id,),
    )


async def set_poll_state(poll_id, state: str):
    await execute(
        "UPDATE availability_polls SET state=$1 WHERE id=$2",
        (state, poll_id),
    )


async def add_poll_message(poll_id, school, channel_id, message_id):
    await execute(
        """
        INSERT INTO availability_poll_messages (poll_id, school, channel_id, message_id)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (poll_id, school)
        DO UPDATE SET channel_id = EXCLUDED.channel_id, message_id = EXCLUDED.message_id
        """,
        (poll_id, school, channel_id, message_id),
    )


async def get_poll_messages(poll_id):
    rows = await fetchall(
        "SELECT school, channel_id, message_id FROM availability_poll_messages WHERE poll_id=$1",
        (poll_id,),
    )
    return [(r["school"], r["channel_id"], r["message_id"]) for r in rows]


# ─────────────────────────────────────────────────────────────
# DB — occurrences
# ─────────────────────────────────────────────────────────────
async def add_occurrences(poll_id, occurrences):
    """occurrences: iterable of (ride_date, ride_type, schools_list)."""
    await executemany(
        "INSERT INTO availability_occurrences (poll_id, ride_date, ride_type, schools) "
        "VALUES ($1, $2, $3, $4) ON CONFLICT DO NOTHING",
        [(poll_id, d, t, ",".join(sch)) for d, t, sch in occurrences],
    )


async def get_occurrences(poll_id, school=None):
    """All (ride_date, ride_type) for the poll, ordered. If `school` is given,
    only the occurrences that school's drivers are needed for."""
    rows = await fetchall(
        "SELECT ride_date, ride_type, schools FROM availability_occurrences "
        "WHERE poll_id=$1 ORDER BY ride_date, ride_type",
        (poll_id,),
    )
    return [
        (r["ride_date"], r["ride_type"])
        for r in rows
        if school is None or school in r["schools"].split(",")
    ]


# ─────────────────────────────────────────────────────────────
# DB — availability entries
# ─────────────────────────────────────────────────────────────
async def replace_entries(poll_id, user_id, school, picks):
    """picks: iterable of (ride_date, ride_type). Replaces ALL of this user's
    entries for the poll with the given set."""
    await execute(
        "DELETE FROM availability_entries WHERE poll_id=$1 AND user_id=$2",
        (poll_id, user_id),
    )
    await executemany(
        """
        INSERT INTO availability_entries (poll_id, user_id, ride_date, ride_type, school)
        VALUES ($1, $2, $3, $4, $5)
        """,
        [(poll_id, user_id, d, t, school) for d, t in picks],
    )


async def get_entries(poll_id, user_id=None):
    """(user_id, ride_date, ride_type, school) for the poll, optionally for one user."""
    query = "SELECT user_id, ride_date, ride_type, school FROM availability_entries WHERE poll_id=$1"
    params = (poll_id,)
    if user_id is not None:
        query += " AND user_id=$2"
        params += (user_id,)
    rows = await fetchall(query + " ORDER BY ride_date, ride_type", params)
    return [(r["user_id"], r["ride_date"], r["ride_type"], r["school"]) for r in rows]


# ─────────────────────────────────────────────────────────────
# DB — assignments
# ─────────────────────────────────────────────────────────────
async def clear_assignments(poll_id):
    await execute("DELETE FROM availability_assignments WHERE poll_id=$1", (poll_id,))


async def write_assignments(poll_id, assignments, assigned_by="auto"):
    """assignments: iterable of (user_id, ride_date, ride_type, school)."""
    await executemany(
        """
        INSERT INTO availability_assignments (poll_id, user_id, ride_date, ride_type, school, assigned_by)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (poll_id, user_id, ride_date, ride_type)
        DO UPDATE SET school = EXCLUDED.school, assigned_by = EXCLUDED.assigned_by
        """,
        [(poll_id, uid, d, t, school, assigned_by) for uid, d, t, school in assignments],
    )


async def remove_assignment(poll_id, user_id, ride_date, ride_type) -> bool:
    row = await fetchone(
        """
        DELETE FROM availability_assignments
        WHERE poll_id=$1 AND user_id=$2 AND ride_date=$3 AND ride_type=$4
        RETURNING 1
        """,
        (poll_id, user_id, ride_date, ride_type),
    )
    return row is not None


async def get_assignments(poll_id):
    rows = await fetchall(
        "SELECT user_id, ride_date, ride_type, school, assigned_by FROM availability_assignments WHERE poll_id=$1",
        (poll_id,),
    )
    return [
        (r["user_id"], r["ride_date"], r["ride_type"], r["school"], r["assigned_by"])
        for r in rows
    ]


async def get_assignments_for_ride(ride_date, ride_type):
    rows = await fetchall(
        "SELECT user_id, school FROM availability_assignments WHERE ride_date=$1 AND ride_type=$2",
        (ride_date, ride_type),
    )
    return [(r["user_id"], r["school"]) for r in rows]


# ─────────────────────────────────────────────────────────────
# Assignment
# ─────────────────────────────────────────────────────────────
def plan_assignments(occurrences_by_school, entries):
    """Even-split assignment, as a pure function.

    occurrences_by_school: {school: [(ride_date, ride_type), ...]} in ride order.
    entries: iterable of (user_id, ride_date, ride_type, school).

    Fills each ride up to its base target (see assign_target); each seat goes to
    the available driver with the fewest assignments so far, then the fewest
    dates offered (so limited-availability drivers aren't crowded out), then the
    lowest user_id.

    Returns (assignments, shortfalls):
      assignments: [(user_id, ride_date, ride_type, school)]
      shortfalls:  [(school, ride_date, ride_type, assigned_count, target)]
    """
    # school -> (ride_date, ride_type) -> [user_id]
    avail = {s: defaultdict(list) for s in occurrences_by_school}
    for user_id, ride_date, ride_type, school in entries:
        if school in avail:
            avail[school][(ride_date, ride_type)].append(user_id)

    assignments = []
    shortfalls = []
    for school, occurrences in occurrences_by_school.items():
        counts = defaultdict(int)
        offered = defaultdict(int)
        for occ_users in avail[school].values():
            for user_id in occ_users:
                offered[user_id] += 1

        for ride_date, ride_type in occurrences:
            candidates = avail[school].get((ride_date, ride_type), [])
            target = assign_target(school, ride_type)
            picked = set()
            while len(picked) < target and len(picked) < len(candidates):
                pool = [u for u in candidates if u not in picked]
                chosen = min(pool, key=lambda u: (counts[u], offered[u], u))
                picked.add(chosen)
                counts[chosen] += 1
                assignments.append((chosen, ride_date, ride_type, school))
            if len(picked) < target:
                shortfalls.append((school, ride_date, ride_type, len(picked), target))

    return assignments, shortfalls


async def auto_assign(poll_id):
    """Full recompute of the poll's assignments (wipes manual edits). Returns the
    rides that came in under target — see plan_assignments."""
    occurrences_by_school = {s: await get_occurrences(poll_id, s) for s in SCHOOLS}
    entries = await get_entries(poll_id)
    assignments, shortfalls = plan_assignments(occurrences_by_school, entries)
    await clear_assignments(poll_id)
    await write_assignments(poll_id, assignments, assigned_by="auto")
    return shortfalls


async def topup_assignment_for_user(bot, poll_id, user_id, school):
    """Fold a driver's freshly-submitted availability into an already-assigned
    schedule: assign them to any occurrence they're available for that is still
    below its base target (see assign_target) for their school.

    Never unassigns anyone. Safe to call on every availability edit — it's a
    no-op unless the poll state is 'assigned' and there is a real gap this
    driver can fill. Syncs any already-sent announcements for the affected rides
    and posts an admin note. The caller refreshes the admin message.

    Returns the list of (ride_date, ride_type) the driver was newly assigned to.
    """
    poll = await get_poll(poll_id)
    if not poll or poll["state"] != "assigned" or school not in SCHOOLS:
        return []

    occurrences = await get_occurrences(poll_id, school)
    user_avail = set(occurrences) & {
        (d, t) for _, d, t, _ in await get_entries(poll_id, user_id)
    }
    if not user_avail:
        return []

    # Current driver count per occurrence for this school, and which of those
    # this driver is already on.
    counts = {occ: 0 for occ in occurrences}
    mine = set()
    for uid, ride_date, ride_type, s, _by in await get_assignments(poll_id):
        occ = (ride_date, ride_type)
        if s != school or occ not in counts:
            continue
        counts[occ] += 1
        if uid == user_id:
            mine.add(occ)

    to_add = sorted(
        occ for occ in user_avail
        if occ not in mine and counts[occ] < assign_target(school, occ[1])
    )
    if not to_add:
        return []

    await write_assignments(
        poll_id, [(user_id, d, t, school) for d, t in to_add], assigned_by="auto-topup"
    )
    for ride_date, ride_type in to_add:
        try:
            await sync_assignments_to_announcements(bot, ride_date, ride_type)
        except Exception as e:
            print(f"[availability] topup announcement sync failed: {e}")

    who = (await _display_names(bot, [user_id]))[user_id]
    emoji, _ = SCHOOL_STYLE.get(school, ("", 0))
    lines = [
        f"🚗 **{emoji} {school} — late availability auto-assigned**",
        f"**{who}** added availability after assignment and was placed on:",
    ]
    lines += [f"• {occurrence_label(d, t)}" for d, t in to_add]
    lines.append("Use the ✏️ Adjust button on the schedule to change this.")
    await _send_admin_note(bot, lines)

    return to_add


# ─────────────────────────────────────────────────────────────
# Announcement pipeline
# ─────────────────────────────────────────────────────────────
async def prefill_announcement_drivers(bot, announcement_id, ride_date, content_category):
    """Reconcile a reactable announcement's driver signups with the current
    availability assignments for its ride:

    - auto-register assigned drivers who aren't signed up yet, and
    - withdraw drivers that were auto-added but are no longer assigned.

    Manually-signed-up drivers (auto_assigned = FALSE) are never touched, and
    extra drivers can still sign up manually. A driver is auto-registered only if
    they have saved seats + phone (`saved_info`); anyone missing that is reported
    to the admin channel and must sign up manually.

    Safe to call repeatedly (idempotent). Runs both when an announcement is sent
    and whenever assignments change (the Assign / Adjust admin buttons).
    """
    if not ride_date or content_category not in ("F", "S"):
        return

    assigned = dict(await get_assignments_for_ride(ride_date, content_category))  # user_id -> school

    existing = await fetchall(
        "SELECT user_id, auto_assigned FROM ride_entries WHERE announcement_id=$1 AND role='driver'",
        (announcement_id,),
    )
    existing_ids = {r["user_id"] for r in existing}
    auto_ids = {r["user_id"] for r in existing if r["auto_assigned"]}

    to_add = [(uid, school) for uid, school in assigned.items() if uid not in existing_ids]
    to_remove = [uid for uid in auto_ids if uid not in assigned]

    if not to_add and not to_remove:
        return

    guild = bot.get_guild(SERVER_ID)
    added, removed = 0, 0
    missing = []       # not in the server — can't register
    incomplete = []    # registered, but no saved seats/phone to fill in

    # ── Register newly assigned drivers ──
    for user_id, school in to_add:
        member = await _resolve_member(guild, user_id)
        if member is None:
            missing.append(user_id)
            continue

        saved = await fetchone(
            "SELECT seats, phone FROM saved_info WHERE user_id=$1", (user_id,)
        )
        has_saved = bool(saved and saved["seats"] is not None)
        # No saved data — register them anyway with blanks; they (or an admin)
        # fill in seats/phone later via the "I'm a Driver" button.
        seats, phone = (saved["seats"], saved["phone"]) if has_saved else (0, "")

        # ON CONFLICT: the user may already have a non-driver row (e.g. a rider
        # signup) or a concurrent reconcile may have inserted them — leave it be.
        async with row_lock:
            row = await fetchone(
                """
                INSERT INTO ride_entries (
                    announcement_id, user_id, school, role, seats, updated_at, phone, info, row_num, auto_assigned
                )
                SELECT $1, $2, $3, 'driver', $4, NOW(), $5, '',
                       COALESCE(MAX(row_num), 0) + 1, TRUE
                FROM ride_entries
                WHERE announcement_id = $1 AND role = 'driver' AND school = $3
                ON CONFLICT (announcement_id, user_id) DO NOTHING
                RETURNING row_num
                """,
                (announcement_id, user_id, school, seats, phone),
            )
        if row is None:
            continue
        if not has_saved:
            incomplete.append(member)
        try:
            await sync_to_sheets(
                member=member,
                announcement_id=announcement_id,
                school=school,
                role="driver",
                seats=seats,
                phone=phone,
                info="",
                count=row["row_num"],
                content_category=content_category,
            )
        except Exception as e:
            print(f"[availability] sheet sync failed for {user_id}: {e}")
        added += 1

    # ── Withdraw drivers that were auto-added but are no longer assigned ──
    for user_id in to_remove:
        entry = await fetchone(
            """
            DELETE FROM ride_entries
            WHERE announcement_id=$1 AND user_id=$2 AND role='driver' AND auto_assigned=TRUE
            RETURNING school, seats, phone, info, row_num
            """,
            (announcement_id, user_id),
        )
        if not entry:
            continue
        school, seats, phone, info, row_num = entry
        member = await _resolve_member(guild, user_id)
        if member is not None:
            try:
                await remove_from_sheets(
                    member, announcement_id, school, "driver", seats, phone,
                    info, row_num, content_category,
                )
            except Exception as e:
                print(f"[availability] sheet removal failed for {user_id}: {e}")
        removed += 1

    if added or removed:
        await refresh_dashboard_for_announcement(bot, announcement_id)

    if added or removed or missing or incomplete:
        lines = [
            f"🔗 **Driver pipeline** · {ride_type_label(content_category)} "
            f"{fmt_ride_date(ride_date)}"
        ]
        if added:
            lines.append(f"• Auto-registered **{added}** assigned driver(s).")
        if removed:
            lines.append(f"• Withdrew **{removed}** driver(s) no longer assigned.")
        if incomplete:
            who = ", ".join(m.display_name for m in incomplete)
            lines.append(
                f"• ⚠️ Registered with **no seats/phone on file** — have them "
                f'press "I\'m a Driver" to fill it in: {who}'
            )
        if missing:
            who = ", ".join(f"<@{uid}>" for uid in missing)
            lines.append(
                f"• ⚠️ Assigned but not in the server — skipped: {who}"
            )
        await _send_admin_note(bot, lines)


async def sync_assignments_to_announcements(bot, ride_date, ride_type) -> int:
    """Reconcile every already-sent reactable announcement whose ride matches this
    occurrence. Returns the number of announcements touched."""
    anns = await fetchall(
        """
        SELECT id FROM announcements
        WHERE state='sent' AND reactable=TRUE
          AND ride_date=$1 AND content_category=$2
        """,
        (ride_date, ride_type),
    )
    for r in anns:
        await prefill_announcement_drivers(bot, r["id"], ride_date, ride_type)
    return len(anns)


async def request_drivers_if_short(bot, announcement_id, ride_date, content_category) -> int:
    """Called when a ride's signup form closes: for each school with fewer driver
    seats than riders on this announcement, post an urgent call for drivers to
    that school's availability channel. Returns how many channels were posted to."""
    if not ride_date or content_category not in ("F", "S"):
        return 0

    rows = await fetchall(
        "SELECT school, role, seats FROM ride_entries WHERE announcement_id=$1",
        (announcement_id,),
    )
    seats = defaultdict(int)
    riders = defaultdict(int)
    for r in rows:
        if r["role"] == "driver":
            seats[r["school"]] += r["seats"] or 0
        elif r["role"] == "rider":
            riders[r["school"]] += 1

    posted = 0
    for school in SCHOOLS:
        short = riders[school] - seats[school]
        if short <= 0:
            continue
        ch = await _channel(bot, AVAILABILITY_CHANNELS.get(school))
        if ch is None:
            continue
        emoji, _ = SCHOOL_STYLE[school]
        try:
            await ch.send(
                f"🚨 **{emoji} {school} drivers needed — "
                f"{ride_type_label(content_category)} {fmt_ride_date(ride_date)}**\n"
                f"Signups just closed with **{seats[school]}** seat(s) for "
                f"**{riders[school]}** rider(s) — short **{short}**. "
                "If you can still drive, register ASAP."
            )
            posted += 1
        except Exception:
            pass
    return posted


# ─────────────────────────────────────────────────────────────
# Rendering
# ─────────────────────────────────────────────────────────────
def _coverage_line(covered: int, total: int) -> str:
    if total == 0:
        return "— no ride dates"
    if covered >= total:
        return f"🟢 {covered}/{total} rides at target"
    gaps = total - covered
    return f"🔴 {covered}/{total} at target · {gaps} short"


def _load_bars(load, names) -> str:
    """load: {user_id: count}. Returns a fenced code block of block-bar rows."""
    if not load:
        return "_nobody assigned yet_"
    rows = []
    for user_id, count in sorted(
        load.items(), key=lambda kv: (-kv[1], names.get(kv[0], "").casefold())
    ):
        label = _trunc(names.get(user_id, str(user_id)), 15).ljust(15)
        rows.append(f"{label} {'█' * count} {count}")
    return "```\n" + "\n".join(rows) + "\n```"


async def render_availability_embeds(bot, poll_id) -> list:
    """Per school: who's available for each ride, and each driver's dates."""
    poll = await get_poll(poll_id)
    if not poll:
        return []
    month = poll["month"]

    school_occ = {s: await get_occurrences(poll_id, s) for s in SCHOOLS}
    entries = await get_entries(poll_id)

    names = await _display_names(bot, [uid for uid, *_ in entries])

    # school -> user_id -> set of (date, type)
    picks = {s: defaultdict(set) for s in SCHOOLS}
    for user_id, ride_date, ride_type, school in entries:
        if school in picks:
            picks[school][user_id].add((ride_date, ride_type))

    embeds = []
    for school in SCHOOLS:
        emoji, color = SCHOOL_STYLE[school]
        by_user = picks[school]

        # By-ride list
        ride_lines = []
        for occ in school_occ[school]:
            drivers = sorted(
                (names.get(uid, str(uid)) for uid, p in by_user.items() if occ in p),
                key=str.casefold,
            )
            target = assign_target(school, occ[1])
            ride_lines.append(
                f"**{occurrence_label(*occ)}** · {len(drivers)} available "
                f"(target {target}) — "
                + (", ".join(drivers) if drivers else "*nobody*")
            )

        # By-driver list
        driver_lines = []
        for uid, p in sorted(
            by_user.items(), key=lambda kv: names.get(kv[0], str(kv[0])).casefold()
        ):
            dates = ", ".join(fmt_ride_date(d) for d, _ in sorted(p))
            driver_lines.append(
                f"• **{names.get(uid, str(uid))}** ({len(p)}) — {dates}"
            )

        body = "**By ride**\n" + (
            "\n".join(ride_lines) if ride_lines else "*No ride dates.*"
        )
        body += "\n\n**By driver**\n" + (
            "\n".join(driver_lines) if driver_lines else "*No responses yet.*"
        )

        embeds.append(
            discord.Embed(
                title=f"{emoji} {school} — Availability · {_month_title(month)}",
                description=body,
                color=color,
            )
        )
    return embeds


async def render_schedule_embeds(bot, poll_id, assignments=None) -> list:
    """Summary embed plus one per-school schedule (coverage, load, unassigned)."""
    poll = await get_poll(poll_id)
    if not poll:
        return []
    month, state = poll["month"], poll["state"]

    school_occ = {s: await get_occurrences(poll_id, s) for s in SCHOOLS}
    entries = await get_entries(poll_id)
    if assignments is None:
        assignments = await get_assignments(poll_id)

    all_ids = [uid for uid, *_ in entries] + [uid for uid, *_ in assignments]
    names = await _display_names(bot, all_ids)

    # school -> occurrence -> [user_id]
    assigned = {s: defaultdict(list) for s in SCHOOLS}
    load = {s: defaultdict(int) for s in SCHOOLS}
    for user_id, ride_date, ride_type, school, _by in assignments:
        if school in assigned:
            assigned[school][(ride_date, ride_type)].append(user_id)
            load[school][user_id] += 1

    submitted = {s: set() for s in SCHOOLS}
    for user_id, ride_date, ride_type, school in entries:
        if school in submitted:
            submitted[school].add(user_id)

    target_bits = ", ".join(
        f"{s} {ride_type_label(t)} ×{n}"
        for (s, t), n in sorted(ASSIGN_TARGETS.items())
    )
    summary = discord.Embed(
        title=f"🚗 Driving Schedule · {_month_title(month)}",
        description=(
            f"Status: **{state}** · drivers auto-assigned toward each ride's base "
            f"target ({target_bits or 'none set'}; all others ×{DEFAULT_ASSIGN_TARGET}), "
            "load spread evenly.\n"
            "Add or swap drivers for a specific week with the ✏️ Adjust button below."
        ),
        color=discord.Color.green(),
    )
    for school in SCHOOLS:
        occ = school_occ[school]
        covered = sum(
            1
            for o in occ
            if len(assigned[school].get(o, [])) >= assign_target(school, o[1])
        )
        emoji, _ = SCHOOL_STYLE[school]
        summary.add_field(
            name=f"{emoji} {school}",
            value=f"{_coverage_line(covered, len(occ))}\n**{len(load[school])}** driver{'s' if len(load[school]) != 1 else ''} assigned",
            inline=True,
        )

    embeds = [summary]
    for school in SCHOOLS:
        emoji, color = SCHOOL_STYLE[school]

        if not school_occ[school]:
            rows = ["_No ride dates._"]
        else:
            rows = []
            for (ride_date, ride_type) in school_occ[school]:
                drivers = assigned[school].get((ride_date, ride_type), [])
                target = assign_target(school, ride_type)
                label = f"{fmt_ride_date(ride_date)}"
                mark = " " if len(drivers) >= target else "⚠"
                cov = f"{len(drivers)}/{target}"
                who = (
                    ", ".join(sorted(names.get(u, str(u)) for u in drivers))
                    if drivers
                    else "NEEDS DRIVERS"
                )
                rows.append(f"{mark} {label:<12}{cov:<6}{who}")
        schedule_block = "```\n" + "\n".join(rows) + "\n```"

        unassigned = sorted(
            (
                names.get(u, str(u))
                for u in submitted[school]
                if load[school].get(u, 0) == 0
            ),
            key=str.casefold,
        )

        desc = schedule_block + "\n**Driver load**\n" + _load_bars(load[school], names)
        if unassigned:
            desc += "\n**Available, not assigned:** " + ", ".join(unassigned)

        embeds.append(
            discord.Embed(
                title=f"{emoji} {school} — Driving Schedule",
                description=desc,
                color=color,
            )
        )
    return embeds


# ─────────────────────────────────────────────────────────────
# Admin message — one live message per poll (availability, then the schedule)
# ─────────────────────────────────────────────────────────────
async def refresh_admin_message(bot, poll_id, repost=False):
    """Render the poll's admin-channel message: the driving schedule once the
    poll is assigned (or has any assignments), the availability list before
    that, with the admin control buttons (AdminPanelView) underneath. Edits the
    stored message in place, posting a new one if it's missing
    or deleted. repost=True deletes the old message and posts a fresh one at the
    bottom of the channel. Returns the message id (or None on failure)."""
    poll = await get_poll(poll_id)
    if not poll:
        return None

    assignments = await get_assignments(poll_id)
    if poll["state"] == "assigned" or assignments:
        embeds = await render_schedule_embeds(bot, poll_id, assignments)
    else:
        embeds = await render_availability_embeds(bot, poll_id)

    admin_ch = await _channel(bot, ADMIN_CHANNEL_ID)
    if admin_ch is None or not embeds:
        return None
    view = AdminPanelView(poll_id, poll["state"])

    msg_id = poll["admin_message_id"]
    if msg_id:
        try:
            msg = await admin_ch.fetch_message(msg_id)
            if not repost:
                await msg.edit(content=None, embeds=embeds, view=view)
                return msg_id
            await msg.delete()
        except Exception:
            pass  # missing/deleted — fall through and post a new one

    msg = await admin_ch.send(embeds=embeds, view=view)
    await execute(
        "UPDATE availability_polls SET admin_message_id=$1 WHERE id=$2",
        (msg.id, poll_id),
    )
    return msg.id


# ─────────────────────────────────────────────────────────────
# Admin actions (behind the admin message's buttons)
# Each returns the ephemeral reply to show the admin.
# ─────────────────────────────────────────────────────────────
async def run_assign(bot, poll) -> str:
    """Auto-assign drivers toward each ride's base target (even load), move the
    schedule to the bottom of the admin channel and sync sent announcements.
    Full recompute — clears manual edits."""
    # Re-read: a confirm prompt may have sat open while someone closed the poll.
    poll = await get_poll(poll["id"])
    if not poll or poll["state"] == "closed":
        return "❌ This availability poll is closed."
    shortfalls = await auto_assign(poll["id"])
    await set_poll_state(poll["id"], "assigned")
    await refresh_admin_message(bot, poll["id"], repost=True)

    # Push the new assignments into any already-sent announcements for these rides
    synced = 0
    for ride_date, ride_type in await get_occurrences(poll["id"]):
        synced += await sync_assignments_to_announcements(bot, ride_date, ride_type)

    if shortfalls:
        lines = "\n".join(
            f"• {school} — {occurrence_label(d, t)} ({got}/{target})"
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

    return (
        f"✅ Auto-assigned drivers for `{poll['month']}` and posted the schedule to the admin channel."
        f"{note}\n\n*Manual edits were cleared — use the ✏️ Adjust button to layer them back on.*"
    )


async def run_adjust(bot, poll_id, ride_date, ride_type, user, add: bool, assigned_by) -> str:
    """Add or remove one driver for a single ride, then refresh the admin
    message and any already-sent announcement for that ride."""
    label = occurrence_label(ride_date, ride_type)
    if add:
        member = user if isinstance(user, discord.Member) else await _resolve_member(
            bot.get_guild(SERVER_ID), user.id
        )
        school = get_school(member) if member else None
        if school not in SCHOOLS:
            return (
                f"❌ {user.display_name} isn't in a school that uses the availability "
                f"system ({', '.join(SCHOOLS)})."
            )
        await write_assignments(
            poll_id, [(user.id, ride_date, ride_type, school)], assigned_by=assigned_by
        )
        verb = "Added"
    else:
        if not await remove_assignment(poll_id, user.id, ride_date, ride_type):
            return f"ℹ️ {user.display_name} was not assigned to {label}."
        verb = "Removed"

    await refresh_admin_message(bot, poll_id)

    # Push the change into any already-sent announcement for this ride
    synced = await sync_assignments_to_announcements(bot, ride_date, ride_type)
    tail = f" Updated {synced} already-sent announcement(s)." if synced else ""
    return f"✅ {verb} {user.display_name} — {label}.{tail}"


async def run_close(bot, poll) -> str:
    """Close the poll: disable every school's dropdown and the admin message's
    Assign / Close buttons."""
    poll = await get_poll(poll["id"])
    if not poll or poll["state"] == "closed":
        return "ℹ️ This availability poll is already closed."
    await set_poll_state(poll["id"], "closed")

    for school, channel_id, message_id in await get_poll_messages(poll["id"]):
        occurrences = await get_occurrences(poll["id"], school)
        closed_view = AvailabilityView(poll["id"], school, occurrences, is_closed=True)
        channel = await _channel(bot, channel_id)
        if not channel:
            continue
        try:
            msg = await channel.fetch_message(message_id)
            await msg.edit(
                content=(
                    f"**🗓 {school} Driver Availability — {poll['month']}**\n"
                    "🔒 This availability poll is closed."
                ),
                view=closed_view,
            )
        except Exception:
            pass

    await refresh_admin_message(bot, poll["id"])
    return f"✅ Closed the availability poll for `{poll['month']}`."


# ─────────────────────────────────────────────────────────────
# Availability View (persistent, driver-facing, one per school)
# ─────────────────────────────────────────────────────────────
class AvailabilityView(discord.ui.View):
    def __init__(self, poll_id, school, occurrences, is_closed: bool):
        super().__init__(timeout=None)
        self.poll_id = str(poll_id)
        self.school = school
        self.occurrences = list(occurrences)[:MAX_OPTIONS]

        # These custom_ids are persisted in already-posted Discord messages —
        # never change their format.
        suffix = f"{self.poll_id}:{school}"

        options = [
            discord.SelectOption(
                label=occurrence_label(ride_date, ride_type),
                value=encode_occurrence(ride_date, ride_type),
            )
            for ride_date, ride_type in self.occurrences
        ] or [discord.SelectOption(label="No ride dates", value="none")]

        self.select = discord.ui.Select(
            placeholder="Closed — availability is no longer being collected"
            if is_closed
            else f"{school} drivers: select every date you can drive…",
            min_values=0,
            max_values=len(options),
            options=options,
            custom_id=f"avail:pick:{suffix}",
            disabled=is_closed or not self.occurrences,
        )
        self.select.callback = self._on_select
        self.add_item(self.select)

        if not is_closed:
            mine = discord.ui.Button(
                label="My availability",
                emoji="📋",
                style=discord.ButtonStyle.secondary,
                custom_id=f"avail:mine:{suffix}",
            )
            mine.callback = self._on_mine
            self.add_item(mine)

            clear = discord.ui.Button(
                label="Clear my availability",
                emoji="🗑",
                style=discord.ButtonStyle.danger,
                custom_id=f"avail:clear:{suffix}",
            )
            clear.callback = self._on_clear
            self.add_item(clear)

    # ──────────────── Helpers ────────────────
    async def _guard(self, interaction: discord.Interaction) -> bool:
        """Returns True if the interaction may proceed. Otherwise a response has
        already been sent."""
        poll = await get_poll(self.poll_id)
        if not poll or poll["state"] == "closed":
            await interaction.response.send_message(
                "❌ This availability poll is closed.", ephemeral=True
            )
            return False

        school = get_school(interaction.user)
        if not school:
            await interaction.response.send_message(
                "❌ You need a school role to submit availability.", ephemeral=True
            )
            return False
        if school != self.school:
            await interaction.response.send_message(
                f"❌ This form is for **{self.school}** drivers — you're in **{school}**. "
                "Use your own school's availability channel.",
                ephemeral=True,
            )
            return False
        return True

    # ──────────────── Callbacks ────────────────
    async def _on_select(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return

        picks = sorted(
            decode_occurrence(v) for v in self.select.values if v != "none"
        )
        await replace_entries(self.poll_id, interaction.user.id, self.school, picks)

        if picks:
            lines = "\n".join(f"• {occurrence_label(d, t)}" for d, t in picks)
            await interaction.response.send_message(
                f"✅ Saved your availability for **{self.school}**:\n{lines}",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "✅ Cleared — you have no dates marked for this month.",
                ephemeral=True,
            )

        # If the schedule for this month is already assigned, fold this driver
        # into any dates that are still uncovered or short on drivers.
        newly_assigned = []
        if picks:
            newly_assigned = await topup_assignment_for_user(
                interaction.client, self.poll_id, interaction.user.id, self.school
            )
        if newly_assigned:
            got = "\n".join(
                f"• {occurrence_label(d, t)}" for d, t in newly_assigned
            )
            await interaction.followup.send(
                "🚗 The driving schedule for this month was already set, so "
                "you've been **assigned to drive** these dates that were short "
                f"on drivers:\n{got}\n\nMessage an admin if you can't make one.",
                ephemeral=True,
            )

        await refresh_admin_message(interaction.client, self.poll_id)

    async def _on_mine(self, interaction: discord.Interaction):
        entries = await get_entries(self.poll_id, interaction.user.id)
        if entries:
            lines = "\n".join(f"• {occurrence_label(d, t)}" for _, d, t, _ in entries)
            await interaction.response.send_message(
                f"📋 Your current availability:\n{lines}", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "📋 You haven't marked any availability for this month yet.",
                ephemeral=True,
            )

    async def _on_clear(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        await replace_entries(self.poll_id, interaction.user.id, self.school, [])
        # Re-render the message so the dropdown drops the caller's highlighted
        # options (Discord keeps a select's shown selection per-user until the
        # component is re-sent). The view carries no default options, so this
        # resets it to "nothing selected".
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(
            "🗑 Cleared your availability for this month.", ephemeral=True
        )
        await refresh_admin_message(interaction.client, self.poll_id)


def _is_admin(interaction: discord.Interaction) -> bool:
    perms = getattr(interaction.user, "guild_permissions", None)
    return bool(perms and perms.manage_messages)


# ─────────────────────────────────────────────────────────────
# Admin control panel (persistent, under the admin message)
# ─────────────────────────────────────────────────────────────
class AdminPanelView(discord.ui.View):
    """Assign / Adjust / Availability / Close buttons. Buttons don't inherit a
    slash command's default_permissions, so every press checks manage_messages."""

    def __init__(self, poll_id, state: str):
        super().__init__(timeout=None)
        self.poll_id = str(poll_id)
        closed = state == "closed"
        self._button(
            "Re-assign drivers" if state == "assigned" else "Assign drivers",
            "🚗", discord.ButtonStyle.primary, "assign", self._on_assign, disabled=closed,
        )
        self._button("Adjust", "✏️", discord.ButtonStyle.secondary, "adjust", self._on_adjust)
        self._button("Availability", "📋", discord.ButtonStyle.secondary, "availability", self._on_availability)
        self._button("Close poll", "🔒", discord.ButtonStyle.danger, "close", self._on_close, disabled=closed)

    def _button(self, label, emoji, style, action, callback, disabled=False):
        button = discord.ui.Button(
            label=label,
            emoji=emoji,
            style=style,
            custom_id=f"avail:admin:{action}:{self.poll_id}",
            disabled=disabled,
        )
        button.callback = callback
        self.add_item(button)

    async def _poll(self, interaction: discord.Interaction):
        """The poll, or None once an error reply has been sent."""
        if not _is_admin(interaction):
            await interaction.response.send_message(
                "❌ Only admins can use these controls.", ephemeral=True
            )
            return None
        poll = await get_poll(self.poll_id)
        if not poll:
            await interaction.response.send_message(
                "❌ This availability poll no longer exists.", ephemeral=True
            )
        return poll

    async def _on_assign(self, interaction: discord.Interaction):
        poll = await self._poll(interaction)
        if not poll:
            return
        if poll["state"] == "closed":
            await interaction.response.send_message(
                f"❌ The `{poll['month']}` poll is closed.", ephemeral=True
            )
            return
        if poll["state"] == "assigned":
            await interaction.response.send_message(
                f"⚠️ Re-assigning recomputes all of `{poll['month']}` and **clears manual "
                "edits** made with ✏️ Adjust. Continue?",
                view=ConfirmView("Re-assign", lambda: run_assign(interaction.client, poll)),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await interaction.followup.send(await run_assign(interaction.client, poll), ephemeral=True)

    async def _on_adjust(self, interaction: discord.Interaction):
        poll = await self._poll(interaction)
        if not poll:
            return
        await interaction.response.send_message(
            f"✏️ **Adjust `{poll['month']}`** — pick a ride and a driver, then **Add** or "
            "**Remove**. Repeat as needed.",
            view=AdjustView(poll["id"], await get_occurrences(poll["id"])),
            ephemeral=True,
        )

    async def _on_availability(self, interaction: discord.Interaction):
        poll = await self._poll(interaction)
        if not poll:
            return
        embeds = await render_availability_embeds(interaction.client, poll["id"])
        await interaction.response.send_message(embeds=embeds, ephemeral=True)

    async def _on_close(self, interaction: discord.Interaction):
        poll = await self._poll(interaction)
        if not poll:
            return
        if poll["state"] == "closed":
            await interaction.response.send_message(
                f"ℹ️ The `{poll['month']}` poll is already closed.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"🔒 Close the `{poll['month']}` poll? Drivers will no longer be able to submit "
            "availability. The schedule can still be adjusted afterwards.",
            view=ConfirmView("Close poll", lambda: run_close(interaction.client, poll)),
            ephemeral=True,
        )


class ConfirmView(discord.ui.View):
    """Ephemeral confirm / cancel. `action` is a no-arg coroutine function that
    returns the reply text."""

    def __init__(self, label, action):
        super().__init__(timeout=120)
        self.action = action
        confirm = discord.ui.Button(label=label, style=discord.ButtonStyle.danger)
        confirm.callback = self._on_confirm
        self.add_item(confirm)
        cancel = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary)
        cancel.callback = self._on_cancel
        self.add_item(cancel)

    async def _on_confirm(self, interaction: discord.Interaction):
        self.stop()
        await interaction.response.edit_message(content="⏳ Working…", view=None)
        await interaction.edit_original_response(content=await self.action())

    async def _on_cancel(self, interaction: discord.Interaction):
        self.stop()
        await interaction.response.edit_message(content="Cancelled.", view=None)


class AdjustView(discord.ui.View):
    """Ephemeral picker: one ride + one driver, then Add or Remove."""

    def __init__(self, poll_id, occurrences):
        super().__init__(timeout=600)
        self.poll_id = poll_id
        self.ride = discord.ui.Select(
            placeholder="Ride…",
            options=[
                discord.SelectOption(
                    label=occurrence_label(d, t), value=encode_occurrence(d, t)
                )
                for d, t in list(occurrences)[:MAX_OPTIONS]
            ],
        )
        self.ride.callback = self._ack
        self.add_item(self.ride)

        self.driver = discord.ui.UserSelect(placeholder="Driver…")
        self.driver.callback = self._ack
        self.add_item(self.driver)

        for label, style, add in (
            ("Add", discord.ButtonStyle.success, True),
            ("Remove", discord.ButtonStyle.danger, False),
        ):
            button = discord.ui.Button(label=label, style=style)
            button.callback = self._on_add if add else self._on_remove
            self.add_item(button)

    async def _ack(self, interaction: discord.Interaction):
        await interaction.response.defer()

    async def _on_add(self, interaction: discord.Interaction):
        await self._apply(interaction, add=True)

    async def _on_remove(self, interaction: discord.Interaction):
        await self._apply(interaction, add=False)

    async def _apply(self, interaction: discord.Interaction, add: bool):
        if not self.ride.values or not self.driver.values:
            await interaction.response.send_message(
                "❌ Pick a ride and a driver first.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        ride_date, ride_type = decode_occurrence(self.ride.values[0])
        reply = await run_adjust(
            interaction.client, self.poll_id, ride_date, ride_type,
            self.driver.values[0], add, interaction.user.display_name,
        )
        await interaction.followup.send(reply, ephemeral=True)


async def restore_views(bot):
    """Re-register persistent views on startup: the dropdown of every poll that
    isn't closed (a closed poll's dropdown is disabled, so it needs no handler),
    and every poll's admin control panel (Adjust still works once closed)."""
    rows = await fetchall(
        """
        SELECT m.poll_id, m.school
        FROM availability_poll_messages m
        JOIN availability_polls p ON p.id = m.poll_id
        WHERE p.state <> 'closed'
        """
    )
    for r in rows:
        occurrences = await get_occurrences(r["poll_id"], r["school"])
        bot.add_view(AvailabilityView(r["poll_id"], r["school"], occurrences, is_closed=False))

    for r in await fetchall(
        "SELECT id, state FROM availability_polls WHERE admin_message_id IS NOT NULL"
    ):
        bot.add_view(AdminPanelView(r["id"], r["state"]))


# ═════════════════════════════════════════════════════════════
# Slash command — everything after creation is done from the
# admin message's buttons
# ═════════════════════════════════════════════════════════════
class AvailabilityCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ─────────────────────────────────────────────────────────────
    # Opens a monthly availability poll and posts a driver-facing
    # dropdown into each school's availability channel.
    # ─────────────────────────────────────────────────────────────
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.command(
        name="availability_create",
        description="Open a monthly driver-availability poll (a dropdown in each school's channel). Once per month.",
    )
    @app_commands.describe(
        month="Month to collect for. 'YYYY-MM', e.g. 2026-09. Every Friday + Sunday that month becomes an option.",
        exclude="Optional dates to skip (holidays). Comma-separated 'YYYY-MM-DD', e.g. 2026-09-25,2026-09-27",
        sunday_host="Optional per-Sunday host: e.g. 2026-09-06J, 2026-09-13G. J=joint, E=Emory service, G=GT service.",
    )
    async def availability_create(
        self,
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

        existing = await get_poll_by_month(month)
        if existing:
            await interaction.response.send_message(
                f"❌ An availability check has already been run for `{month}` (state: {existing['state']}). "
                "A month can only be sent once. Use the buttons on its message in the admin channel to view, assign, adjust or close it.",
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
                sunday_hosts = parse_sunday_hosts(sunday_host)
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
            (d, t, schools_for_occurrence(d, t, sunday_hosts))
            for d, t in occurrences
        ]

        # Resolve each school's channel up front
        channels = {}
        for school in SCHOOLS:
            ch = await _channel(self.bot, AVAILABILITY_CHANNELS.get(school))
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

        poll_id = await create_poll(month)
        await add_occurrences(poll_id, occ_rows)

        posted = []
        for school, channel in channels.items():
            school_occ = [(d, t) for d, t, sch in occ_rows if school in sch]
            date_lines = "\n".join(
                f"• {occurrence_label(d, t)}" for d, t in school_occ
            )
            view = AvailabilityView(poll_id, school, school_occ, is_closed=False)
            self.bot.add_view(view)
            msg = await channel.send(
                content=(
                    f"**🗓 {school} Driver Availability — {month}**\n"
                    "Select **every** date you're able to drive this month. "
                    "Re-selecting replaces your previous answer.\n\n"
                    f"{date_lines}"
                ),
                view=view,
            )
            await add_poll_message(poll_id, school, channel.id, msg.id)
            posted.append(f"{school} → {channel.mention}")

        # Live availability display in the admin channel
        await refresh_admin_message(self.bot, poll_id)

        missing = [s for s in SCHOOLS if s not in channels]
        note = f"\n⚠️ No channel for: {', '.join(missing)}" if missing else ""

        host_note = ""
        if sunday_hosts:
            rows = []
            for d in sorted(sunday_hosts):
                sch = sunday_hosts[d]
                who = ", ".join(sch) + ("" if len(sch) > 1 else " only")
                rows.append(f"• {occurrence_label(d, 'S')} → **{who}**")
            host_note = "\n\n**Sunday services — drivers needed from:**\n" + "\n".join(rows)

        await interaction.followup.send(
            f"✅ Availability poll created for `{month}` (`{poll_id}`).\n"
            + "\n".join(posted)
            + "\nA live availability display was posted to the admin channel — use its "
            "buttons to assign, adjust, view availability and close the poll."
            + host_note
            + note,
            ephemeral=True,
        )
