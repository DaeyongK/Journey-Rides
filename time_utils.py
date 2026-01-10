from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")
UTC = timezone.utc


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
    - '🔒 Requests are now closed for this announcement.'
    """
    if not isinstance(end_at, datetime):
        return "⏳ Closing time unknown"

    if end_at.tzinfo is None:
        end_at = end_at.replace(tzinfo=timezone.utc)

    now_utc = datetime.now(timezone.utc)

    if end_at <= now_utc:
        return "🔒 Requests are now closed for this announcement."

    ts = int(end_at.timestamp())
    return f"⏳ Requests close <t:{ts}:R>"
