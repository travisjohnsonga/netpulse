"""
Timezone conversion at the report-schedule boundary.

The backend stays UTC end to end: ``ReportSchedule.hour`` / ``day_of_week`` /
``day_of_month`` are stored in UTC and the scheduler compares UTC wall-clock
(``timezone.now()`` with ``TIME_ZONE="UTC"``), which is deterministic and does not
depend on any user's locale. The ONLY place timezones are applied is here, at the
API boundary — converting between the requesting user's IANA timezone
(``UserPreferences.timezone``) and UTC, mirroring how ``temperature_unit`` is
stored canonical (Celsius) and converted only for display.

When the local↔UTC conversion crosses a UTC day boundary (e.g. 19:00 America/
Chicago = 01:00 UTC the next day) the day-of-week / day-of-month are shifted too,
so a *Weekly Monday 19:00 CST* schedule fires at the right UTC instant.

DST tradeoff (fixed-UTC-hour storage)
-------------------------------------
Conversion uses the proper IANA zone on a *reference date* (today), so it picks
the correct current offset (CST -6 vs CDT -5). Because the stored value is a
single fixed UTC hour with no date, the real local fire time shifts by one hour
across a DST transition (a 19:00 schedule entered in CDT becomes 18:00 local after
the fall-back, since the stored UTC hour does not move). The display side always
converts the stored UTC hour back with the *current* offset, so the UI never lies
about when the job will actually run — but the originally-typed local hour is not
auto-recomputed. This is the accepted tradeoff of keeping ``hour`` a fixed UTC int
(the alternative — store local intent + tz and recompute every tick — would change
the scheduler's UTC-only comparison, which we deliberately keep). Day-boundary
month rollover (day_of_month near the 1st/end of month) is not perfectly modeled;
day_of_month is constrained to 1–28 and the rare cross-month shift is clamped.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone

_UTC = ZoneInfo("UTC")


def resolve_zone(tzname: str | None) -> ZoneInfo:
    """An IANA ZoneInfo for ``tzname``, falling back to UTC for blank/invalid."""
    if not tzname:
        return _UTC
    try:
        return ZoneInfo(tzname)
    except (ZoneInfoNotFoundError, ValueError):
        return _UTC


def _is_utc(tz: ZoneInfo) -> bool:
    return tz.key in ("UTC", "Etc/UTC")


def _shift(hour, day_of_week, day_of_month, src: ZoneInfo, dst: ZoneInfo, ref_date):
    """Convert (hour, dow, dom) from ``src`` tz to ``dst`` tz on ``ref_date``."""
    src_dt = datetime(ref_date.year, ref_date.month, ref_date.day, hour, tzinfo=src)
    dst_dt = src_dt.astimezone(dst)
    delta = (dst_dt.date() - src_dt.date()).days  # -1, 0, or +1
    new_dow = (day_of_week + delta) % 7
    new_dom = day_of_month + delta
    # day_of_month is constrained to 1..28; clamp the rare cross-month shift
    # (true month-boundary rollover isn't modeled — see module docstring).
    if new_dom < 1:
        new_dom = 28
    elif new_dom > 28:
        new_dom = 1
    return dst_dt.hour, new_dow, new_dom


def local_to_utc(hour, day_of_week, day_of_month, tzname, ref_date=None):
    """User-local (hour, dow, dom) → UTC for storage. UTC users pass through."""
    tz = resolve_zone(tzname)
    if _is_utc(tz):
        return hour, day_of_week, day_of_month
    ref = ref_date or timezone.now().astimezone(tz).date()
    return _shift(hour, day_of_week, day_of_month, tz, _UTC, ref)


def utc_to_local(hour, day_of_week, day_of_month, tzname, ref_date=None):
    """Stored UTC (hour, dow, dom) → user-local for display. UTC users pass through."""
    tz = resolve_zone(tzname)
    if _is_utc(tz):
        return hour, day_of_week, day_of_month
    ref = ref_date or timezone.now().date()
    return _shift(hour, day_of_week, day_of_month, _UTC, tz, ref)
