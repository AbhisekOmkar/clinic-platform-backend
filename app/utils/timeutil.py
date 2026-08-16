"""Clinic-local time handling.

Every user-facing date/time is computed in the clinic timezone (Asia/Kolkata).
Storage is naive-UTC datetimes (Mongo convention). The "today shifts to
tomorrow" class of bugs comes from mixing these up, so all conversions live
here and the rest of the codebase never calls datetime.now() directly.
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.config.settings import settings

UTC = ZoneInfo("UTC")


def clinic_tz() -> ZoneInfo:
    return ZoneInfo(settings.clinic_timezone)


def now_utc() -> datetime:
    return datetime.now(UTC)


def now_local() -> datetime:
    return datetime.now(clinic_tz())


def today_local() -> date:
    return now_local().date()


def local_to_utc(dt_local: datetime) -> datetime:
    """Attach clinic tz if naive, convert to naive UTC for storage."""
    if dt_local.tzinfo is None:
        dt_local = dt_local.replace(tzinfo=clinic_tz())
    return dt_local.astimezone(UTC).replace(tzinfo=None)


def utc_to_local(dt_utc: datetime) -> datetime:
    """Interpret naive datetime as UTC and convert to clinic-local (aware)."""
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=UTC)
    return dt_utc.astimezone(clinic_tz())


def combine_local(d: date, hm: str) -> datetime:
    """'14:30' on a local date -> aware local datetime."""
    hour, minute = parse_hm(hm)
    return datetime.combine(d, time(hour, minute), tzinfo=clinic_tz())


def parse_hm(hm: str) -> tuple[int, int]:
    hour, minute = hm.split(":")
    return int(hour), int(minute)


def minutes_of_day(hm: str) -> int:
    hour, minute = parse_hm(hm)
    return hour * 60 + minute


def hm_of_minutes(total: int) -> str:
    return f"{total // 60:02d}:{total % 60:02d}"


def parse_local_date(value: str) -> date:
    return date.fromisoformat(value)


def date_range(start: date, days: int) -> list[date]:
    return [start + timedelta(days=i) for i in range(days)]


def format_slot_display(d: date, hm: str) -> str:
    """Human/agent friendly: 'Mon 17 Aug, 10:30 AM'."""
    hour, minute = parse_hm(hm)
    dt = datetime.combine(d, time(hour, minute))
    return dt.strftime("%a %d %b, %I:%M %p").replace(" 0", " ")
