import os
import uuid
from collections import defaultdict
from datetime import date, datetime

import discord

from dashboard import refresh_dashboard_for_announcement
from db import execute, executemany, fetchall, fetchone
from exporter import remove_from_sheets, sync_to_sheets
from time_utils import fmt_ride_date, ride_type_label
from dotenv import load_dotenv
load_dotenv()

# ─────────────────────────────────────────────────────────────
# Config
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

# Per-school embed identity (emoji + colour)
SCHOOL_STYLE = {
    "GT":    ("🐝", 0xB3A369),   # Georgia Tech gold
    "Emory": ("🦅", 0x012169),   # Emory blue
}

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


def assign_target(school, ride_type) -> int:
    return ASSIGN_TARGETS.get((school, ride_type), DEFAULT_ASSIGN_TARGET)


# Sunday-service host code -> which schools' drivers are needed. A service held on
# one campus needs the *other* campus's drivers to bring their students over.
HOST_CODE_SCHOOLS = {
    "J": ["GT", "Emory"],   # joint service — drivers from both
    "E": ["GT"],            # Emory-hosted service — GT drivers bring GT students
    "G": ["Emory"],         # GT-hosted service — Emory drivers bring Emory students
}
HOST_CODE_LABEL = {
    "J": "joint service",
    "E": "Emory service",
    "G": "GT service",
}


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
            d = date.fromisoformat(token[:-1].strip())
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


# ─────────────────────────────────────────────────────────────
# Occurrence value encoding (used by the select menu options)
# "YYYY-MM-DD|F"  <->  (date, "F")
# ─────────────────────────────────────────────────────────────
def encode_occurrence(ride_date, ride_type: str) -> str:
    return f"{ride_date.isoformat()}|{ride_type}"


def decode_occurrence(value: str):
    iso, ride_type = value.split("|", 1)
    return date.fromisoformat(iso), ride_type


def occurrence_label(ride_date, ride_type: str) -> str:
    return f"{fmt_ride_date(ride_date)} · {ride_type_label(ride_type)}"


# ─────────────────────────────────────────────────────────────
# Poll CRUD
# ─────────────────────────────────────────────────────────────
async def create_poll(month: str) -> str:
    poll_id = str(uuid.uuid4())
    await execute(
        "INSERT INTO availability_polls (id, month) VALUES ($1, $2)",
        (poll_id, month),
    )
    return poll_id


_POLL_COLUMNS = "id, month, state, admin_message_id, admin_availability_message_id"


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


async def list_restorable_polls():
    return await fetchall(
        "SELECT id, state FROM availability_polls WHERE state IN ('open', 'assigned')"
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


async def list_restorable_poll_messages():
    """(poll_id, school, state) for every per-school dropdown that should have its
    view re-registered on startup."""
    rows = await fetchall(
        """
        SELECT m.poll_id, m.school, p.state
        FROM availability_poll_messages m
        JOIN availability_polls p ON p.id = m.poll_id
        WHERE p.state IN ('open', 'assigned', 'closed')
        """
    )
    return [(r["poll_id"], r["school"], r["state"]) for r in rows]


async def set_admin_message_id(poll_id, message_id):
    await execute(
        "UPDATE availability_polls SET admin_message_id=$1 WHERE id=$2",
        (message_id, poll_id),
    )


async def set_admin_availability_message_id(poll_id, message_id):
    await execute(
        "UPDATE availability_polls SET admin_availability_message_id=$1 WHERE id=$2",
        (message_id, poll_id),
    )


# ─────────────────────────────────────────────────────────────
# Occurrences
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


async def occurrence_exists(poll_id, ride_date, ride_type) -> bool:
    row = await fetchone(
        "SELECT 1 FROM availability_occurrences WHERE poll_id=$1 AND ride_date=$2 AND ride_type=$3",
        (poll_id, ride_date, ride_type),
    )
    return row is not None


# ─────────────────────────────────────────────────────────────
# Availability entries
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


async def get_user_entries(poll_id, user_id):
    rows = await fetchall(
        "SELECT ride_date, ride_type FROM availability_entries WHERE poll_id=$1 AND user_id=$2 ORDER BY ride_date, ride_type",
        (poll_id, user_id),
    )
    return [(r["ride_date"], r["ride_type"]) for r in rows]


async def get_entries(poll_id):
    rows = await fetchall(
        "SELECT user_id, ride_date, ride_type, school FROM availability_entries WHERE poll_id=$1",
        (poll_id,),
    )
    return [(r["user_id"], r["ride_date"], r["ride_type"], r["school"]) for r in rows]


# ─────────────────────────────────────────────────────────────
# Assignments
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


async def add_assignment(poll_id, user_id, ride_date, ride_type, school, assigned_by):
    await write_assignments(
        poll_id, [(user_id, ride_date, ride_type, school)], assigned_by=assigned_by
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


# ─────────────────────────────────────────────────────────────
# Announcement pipeline:
# who is scheduled to drive a given ride occurrence
# ─────────────────────────────────────────────────────────────
async def get_assignments_for_ride(ride_date, ride_type):
    rows = await fetchall(
        "SELECT user_id, school FROM availability_assignments WHERE ride_date=$1 AND ride_type=$2",
        (ride_date, ride_type),
    )
    return [(r["user_id"], r["school"]) for r in rows]


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
    and whenever assignments change (`/availability_assign`, `/availability_adjust`).
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
            missing.append((user_id, None))
            continue

        saved = await fetchone(
            "SELECT seats, phone FROM saved_info WHERE user_id=$1", (user_id,)
        )
        if saved and saved["seats"] is not None:
            seats, phone = saved["seats"], saved["phone"]
        else:
            # No saved data — register them anyway with blanks; they (or an admin)
            # fill in seats/phone later via the "I'm a Driver" button.
            seats, phone = 0, ""
            incomplete.append((user_id, member))

        row = await fetchone(
            """
            INSERT INTO ride_entries (
                announcement_id, user_id, school, role, seats, updated_at, phone, info, row_num, auto_assigned
            )
            SELECT $1, $2, $3, 'driver', $4, NOW(), $5, '',
                   COALESCE(MAX(row_num), 0) + 1, TRUE
            FROM ride_entries
            WHERE announcement_id = $1 AND role = 'driver' AND school = $3
            RETURNING row_num
            """,
            (announcement_id, user_id, school, seats, phone),
        )
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
        admin_ch = await _admin_channel(bot)
        if admin_ch:
            lines = [
                f"🔗 **Driver pipeline** · {ride_type_label(content_category)} "
                f"{fmt_ride_date(ride_date)}"
            ]
            if added:
                lines.append(f"• Auto-registered **{added}** assigned driver(s).")
            if removed:
                lines.append(f"• Withdrew **{removed}** driver(s) no longer assigned.")
            if incomplete:
                who = ", ".join(m.display_name for _, m in incomplete)
                lines.append(
                    f"• ⚠️ Registered with **no seats/phone on file** — have them "
                    f'press "I\'m a Driver" to fill it in: {who}'
                )
            if missing:
                who = ", ".join(f"<@{uid}>" for uid, _ in missing)
                lines.append(
                    f"• ⚠️ Assigned but not in the server — skipped: {who}"
                )
            try:
                await admin_ch.send("\n".join(lines))
            except Exception:
                pass


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


# ─────────────────────────────────────────────────────────────
# Auto-assignment
# ─────────────────────────────────────────────────────────────
async def auto_assign(poll_id):
    """Even-split assignment: fill each ride up to its base target (see
    ASSIGN_TARGETS / assign_target), each seat going to the available driver with
    the fewest running assignments, then the most-constrained driver, then
    user_id. Full recompute — wipes any existing assignment rows first.

    Returns the rides that came in under target as
    (school, ride_date, ride_type, assigned_count, target).
    """
    entries = await get_entries(poll_id)

    # school -> (ride_date, ride_type) -> [user_id]
    avail = {s: defaultdict(list) for s in SCHOOLS}
    for user_id, ride_date, ride_type, school in entries:
        if school in avail:
            avail[school][(ride_date, ride_type)].append(user_id)

    assignments = []
    shortfalls = []

    for school in SCHOOLS:
        occurrences = await get_occurrences(poll_id, school)
        counts = defaultdict(int)
        # How many occurrences each driver offered — used to favour drivers with
        # limited availability so they aren't crowded out by always-available ones.
        offered = defaultdict(int)
        for occ_users in avail[school].values():
            for user_id in occ_users:
                offered[user_id] += 1

        for (ride_date, ride_type) in occurrences:
            candidates = list(avail[school].get((ride_date, ride_type), []))
            target = assign_target(school, ride_type)
            picked = set()
            while len(picked) < target and len(picked) < len(candidates):
                pool = [u for u in candidates if u not in picked]
                chosen = min(pool, key=lambda u: (counts[u], offered[u], u))
                picked.add(chosen)
                counts[chosen] += 1
                assignments.append((chosen, ride_date, ride_type, school))
            if len(picked) < target:
                shortfalls.append(
                    (school, ride_date, ride_type, len(picked), target)
                )

    await clear_assignments(poll_id)
    await write_assignments(poll_id, assignments, assigned_by="auto")

    return shortfalls


async def topup_assignment_for_user(bot, poll_id, user_id, school):
    """Fold a driver's freshly-submitted availability into an already-assigned
    schedule: assign them to any occurrence they're available for that is still
    below its base target (see assign_target) for their school.

    Never unassigns anyone. Safe to call on every availability edit — it's a
    no-op unless the poll state is 'assigned' and there is a real gap this
    driver can fill. Also refreshes the admin schedule message, syncs any
    already-sent announcements for the affected rides, and posts an admin note.

    Returns the list of (ride_date, ride_type) the driver was newly assigned to.
    """
    poll = await get_poll(poll_id)
    if not poll or poll["state"] != "assigned" or school not in SCHOOLS:
        return []

    occurrences = await get_occurrences(poll_id, school)
    if not occurrences:
        return []
    occ_set = set(occurrences)

    user_avail = occ_set & set(await get_user_entries(poll_id, user_id))
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

    for ride_date, ride_type in to_add:
        await add_assignment(
            poll_id, user_id, ride_date, ride_type, school, "auto-topup"
        )

    await refresh_admin_message(bot, poll_id)
    for ride_date, ride_type in to_add:
        try:
            await sync_assignments_to_announcements(bot, ride_date, ride_type)
        except Exception as e:
            print(f"[availability] topup announcement sync failed: {e}")

    admin_ch = await _admin_channel(bot)
    if admin_ch:
        who = (await _display_names(bot, [user_id])).get(user_id, str(user_id))
        emoji, _ = SCHOOL_STYLE.get(school, ("", 0))
        lines = [
            f"🚗 **{emoji} {school} — late availability auto-assigned**",
            f"**{who}** added availability after assignment and was placed on:",
        ]
        lines += [f"• {occurrence_label(d, t)}" for d, t in to_add]
        lines.append("Use `/availability_adjust` to change this.")
        try:
            await admin_ch.send("\n".join(lines))
        except Exception:
            pass

    return to_add


# ─────────────────────────────────────────────────────────────
# Member name resolution
# ─────────────────────────────────────────────────────────────
async def _display_names(bot, user_ids):
    names = {}
    guild = bot.get_guild(SERVER_ID)
    for user_id in set(user_ids):
        member = None
        if guild:
            member = guild.get_member(user_id)
            if member is None:
                try:
                    member = await guild.fetch_member(user_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    member = None
        names[user_id] = member.display_name if member else f"Unknown ({user_id})"
    return names


# ─────────────────────────────────────────────────────────────
# Rendering helpers
# ─────────────────────────────────────────────────────────────
def _month_title(month: str) -> str:
    """'2026-09' -> 'September 2026'."""
    try:
        return datetime.strptime(month, "%Y-%m").strftime("%B %Y")
    except ValueError:
        return month


def _trunc(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


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


# ─────────────────────────────────────────────────────────────
# Rendering — availability list (pre-assignment)
# ─────────────────────────────────────────────────────────────
async def render_availability_embeds(bot, poll_id) -> list:
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


# ─────────────────────────────────────────────────────────────
# Rendering — driving schedule (post-assignment)
# ─────────────────────────────────────────────────────────────
async def render_schedule_embeds(bot, poll_id) -> list:
    poll = await get_poll(poll_id)
    if not poll:
        return []
    month, state = poll["month"], poll["state"]

    school_occ = {s: await get_occurrences(poll_id, s) for s in SCHOOLS}
    entries = await get_entries(poll_id)
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
            "Add or swap drivers for a specific week with `/availability_adjust`."
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
# Admin schedule message helpers
# ─────────────────────────────────────────────────────────────
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


async def _admin_channel(bot):
    return await _channel(bot, ADMIN_CHANNEL_ID)


# ─────────────────────────────────────────────────────────────
# "Drivers needed" call-out — only when a closing ride is short
# on driver seats vs riders (no call-out at assignment time).
# ─────────────────────────────────────────────────────────────
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


async def post_or_refresh_admin_message(bot, poll_id):
    """Creates the admin schedule message if it doesn't exist yet, otherwise
    edits it in place. Returns the message id (or None on failure)."""
    poll = await get_poll(poll_id)
    if not poll:
        return None

    embeds = await render_schedule_embeds(bot, poll_id)
    if not embeds:
        return None

    admin_ch = await _admin_channel(bot)
    if admin_ch is None:
        return None

    admin_msg_id = poll["admin_message_id"]
    if admin_msg_id:
        try:
            msg = await admin_ch.fetch_message(admin_msg_id)
            await msg.edit(embeds=embeds, content=None)
            return admin_msg_id
        except Exception:
            pass  # fall through and repost

    msg = await admin_ch.send(embeds=embeds)
    await set_admin_message_id(poll_id, msg.id)
    return msg.id


async def refresh_admin_message(bot, poll_id):
    poll = await get_poll(poll_id)
    if not poll or not poll["admin_message_id"]:
        return
    await post_or_refresh_admin_message(bot, poll_id)


async def post_or_refresh_admin_availability_message(bot, poll_id):
    """Admin-channel message listing every driver's availability. Created on poll
    creation and edited in place as drivers respond."""
    poll = await get_poll(poll_id)
    if not poll:
        return None

    embeds = await render_availability_embeds(bot, poll_id)
    if not embeds:
        return None

    admin_ch = await _admin_channel(bot)
    if admin_ch is None:
        return None

    msg_id = poll["admin_availability_message_id"]
    if msg_id:
        try:
            msg = await admin_ch.fetch_message(msg_id)
            await msg.edit(content=None, embeds=embeds)
            return msg_id
        except Exception:
            pass  # fall through and repost

    msg = await admin_ch.send(embeds=embeds)
    await set_admin_availability_message_id(poll_id, msg.id)
    return msg.id


async def refresh_admin_availability_message(bot, poll_id):
    poll = await get_poll(poll_id)
    if not poll or not poll["admin_availability_message_id"]:
        return
    await post_or_refresh_admin_availability_message(bot, poll_id)
