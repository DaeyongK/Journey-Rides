from calendar import monthrange
from datetime import date, datetime, timezone, timedelta
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")
UTC = timezone.utc

# Python's weekday(): Monday=0 ... Sunday=6
_FRIDAY = 4
_SUNDAY = 6

_RIDE_TYPE_LABELS = {"F": "Friday PM", "S": "Sunday Service", "E": "Special Event"}

# Categories a reactable announcement can carry. "F" and "S" sync live to the
# Google Sheet; "E" (Special Event) does not — it only supports the manual
# "Export Snapshot" button on the admin dashboard.
ANNOUNCEMENT_CATEGORIES = ("F", "S", "E")
SHEET_CATEGORIES = ("F", "S")


# Whether signups for this category are pushed to / removed from Google Sheets
def syncs_to_sheets(content_category) -> bool:
    return content_category in SHEET_CATEGORIES


# Returns the current UTC timestamp
def now():
    return datetime.now(timezone.utc)


# Returns the cutoff ISO timestamp for announcements older than the specified number of days
def get_cutoff_datetime(days=180):
    return datetime.now(timezone.utc) - timedelta(days=days)


# Parses 'YYYY-MM-DD HH:MM' in US/Eastern and returns datetime_utc
def parse_to_utc_iso(s: str):
    dt = datetime.strptime(s, "%Y-%m-%d %H:%M")
    dt = dt.replace(tzinfo=EASTERN)
    dt_utc = dt.astimezone(UTC)
    return dt_utc


# Formats an ISO 8601 datetime string into Eastern Time
def fmt_time(value):
    try:
        if isinstance(value, datetime):
            dt = value
        else:
            dt = datetime.fromisoformat(value)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)

        return dt.astimezone(EASTERN).strftime("%Y-%m-%d %I:%M %p ET")
    except Exception:
        return str(value)



# Formats the closing time for an announcement, returns Discord relative timestamp or closed status
def format_close_time(end_at: datetime) -> str:
    """
    Returns:
    - '⏳ Requests close <t:UNIX:R>'
    - '🔒 Requests for this announcement have closed. If you need to drop or request any necessary changes, please text in [#rides-logistics](https://discord.com/channels/1414800603686768676/1460658935001256028).'
    """
    if not isinstance(end_at, datetime):
        return "⏳ Closing time unknown"

    if end_at.tzinfo is None:
        end_at = end_at.replace(tzinfo=timezone.utc)

    now_utc = datetime.now(timezone.utc)

    if end_at <= now_utc:
        return "🔒 Requests for this announcement have closed. If you need to drop or request any necessary changes, please text in [#rides-logistics](https://discord.com/channels/1414800603686768676/1460658935001256028)."

    ts = int(end_at.timestamp())
    return f"⏳ Requests close <t:{ts}:R>"


# ─────────────────────────────────────────────────────────────
# Monthly availability helpers
# ─────────────────────────────────────────────────────────────

# Parses 'YYYY-MM' and returns (year, month), raising ValueError on bad input
def parse_month(month_str: str):
    dt = datetime.strptime(month_str.strip(), "%Y-%m")
    return dt.year, dt.month


# Returns every Friday PM ('F') and Sunday Service ('S') ride occurrence in the
# given 'YYYY-MM' month, as a chronologically sorted list of (date, ride_type).
def month_ride_dates(month_str: str):
    year, month = parse_month(month_str)
    _, days_in_month = monthrange(year, month)

    occurrences = []
    for day in range(1, days_in_month + 1):
        d = date(year, month, day)
        if d.weekday() == _FRIDAY:
            occurrences.append((d, "F"))
        elif d.weekday() == _SUNDAY:
            occurrences.append((d, "S"))

    occurrences.sort(key=lambda o: (o[0], o[1]))
    return occurrences


# Human label for a ride type code
def ride_type_label(ride_type: str) -> str:
    return _RIDE_TYPE_LABELS.get(ride_type, ride_type)


# Returns 'F' for a Friday, 'S' for a Sunday, or None for any other weekday
def ride_type_for_date(d) -> str | None:
    if isinstance(d, datetime):
        d = d.date()
    weekday = d.weekday()
    if weekday == _FRIDAY:
        return "F"
    if weekday == _SUNDAY:
        return "S"
    return None


# Formats a ride date like 'Fri 09/05'
def fmt_ride_date(d) -> str:
    if isinstance(d, datetime):
        d = d.date()
    return d.strftime("%a %m/%d")
