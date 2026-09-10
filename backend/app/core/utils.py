from datetime import datetime, timezone, timedelta


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


BEIJING_TIMEZONE = timezone(timedelta(hours=8))


def beijing_now() -> str:
    return datetime.now(BEIJING_TIMEZONE).isoformat()
