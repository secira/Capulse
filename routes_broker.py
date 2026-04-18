from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))


def _format_ist(dt, fmt='%d %b %Y, %I:%M %p'):
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST).strftime(fmt)
