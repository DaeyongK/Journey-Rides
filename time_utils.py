from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")
UTC = timezone.utc


# Returns the current UTC timestamp as an ISO 8601 formatted string.
def now():
    return datetime.now(UTC).isoformat()


# Returns the cutoff ISO timestamp string for announcements older than the specified number of days.
def get_cutoff_iso(days=180):
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


# Parses 'YYYY-MM-DD HH:MM' in US/Eastern and returns (iso_utc, datetime_utc)
def parse_to_utc_iso(s: str):
    dt = datetime.strptime(s, "%Y-%m-%d %H:%M")
    dt = dt.replace(tzinfo=EASTERN)
    dt_utc = dt.astimezone(UTC)
    return dt_utc.isoformat(), dt_utc


# Formats an ISO 8601 datetime string into Eastern Time
def fmt_time(iso_str):
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)

        return dt.astimezone(EASTERN).strftime("%Y-%m-%d %I:%M %p ET")
    except Exception:
        return iso_str


# Formats the closing time for an announcement, returns Discord relative timestamp or closed status
def format_close_time(end_at_iso: str) -> str:
    """
    Returns:
    - '⏳ Requests close <t:UNIX:R>'
    - '🔒 Requests are now closed for this announcement.'
    """
    try:
        end_utc = datetime.fromisoformat(end_at_iso)

        # If stored naive, assume UTC
        if end_utc.tzinfo is None:
            end_utc = end_utc.replace(tzinfo=timezone.utc)

        now_utc = datetime.now(timezone.utc)

        if end_utc <= now_utc:
            return "🔒 Requests are now closed for this announcement."

        ts = int(end_utc.timestamp())
        return f"⏳ Requests close <t:{ts}:R>"

    except Exception:
        return "⏳ Closing time unknown"
