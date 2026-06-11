"""Helpers for parsing and evaluating provider active windows."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone, tzinfo
import re


_WINDOW_RE = re.compile(r"^(?P<start>\d{2}:\d{2})-(?P<end>\d{2}:\d{2})$")
_MIDNIGHT = time(0, 0)


def parse_windows(raw: list[str]) -> list[tuple[time, time]]:
    """Parse active-window strings into normalized time tuples."""
    if not raw:
        return []

    parsed: list[tuple[time, time]] = []
    for item in raw:
        if not isinstance(item, str):
            raise ValueError("Active window entries must be strings")
        match = _WINDOW_RE.fullmatch(item.strip())
        if match is None:
            raise ValueError(f"Invalid active window: {item}")

        start_str = match.group("start")
        end_str = match.group("end")
        start = _parse_boundary(start_str, allow_end_of_day=False)
        end = _parse_boundary(end_str, allow_end_of_day=True)

        if start == end and end_str != "24:00":
            raise ValueError(f"Active window start and end must differ: {item}")

        if end_str == "24:00":
            parsed.append((start, _MIDNIGHT))
            continue
        if end > start:
            parsed.append((start, end))
            continue

        parsed.append((start, _MIDNIGHT))
        parsed.append((_MIDNIGHT, end))
    return parsed


def is_active(now: datetime, windows: list[tuple[time, time]], tz: tzinfo | None) -> bool:
    """Return True when the current instant falls within any active window."""
    if not windows:
        return True

    local_now = _coerce_now(now, tz)
    current_time = local_now.timetz().replace(tzinfo=None)
    for start, end in windows:
        if _is_all_day(start, end):
            return True
        if end == _MIDNIGHT and current_time >= start:
            return True
        if start <= current_time < end:
            return True
    return False


def next_active_start(
    now: datetime, windows: list[tuple[time, time]], tz: tzinfo | None
) -> datetime | None:
    """Return the next datetime when an inactive route becomes active."""
    if not windows:
        return None
    if is_active(now, windows, tz):
        return now

    effective_tz = _effective_tz(tz)
    local_now = _coerce_now(now, effective_tz)
    candidates: list[datetime] = []
    for start, end in windows:
        if _is_all_day(start, end):
            return now
        candidates.append(_next_start_after(local_now, start, end))
    return min(candidates) if candidates else None


def _parse_boundary(value: str, *, allow_end_of_day: bool) -> time:
    hour_str, minute_str = value.split(":", 1)
    hour = int(hour_str)
    minute = int(minute_str)
    if allow_end_of_day and hour == 24 and minute == 0:
        return _MIDNIGHT
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"Invalid active window boundary: {value}")
    return time(hour, minute)


def _effective_tz(tz: tzinfo | None) -> tzinfo:
    if tz is not None:
        return tz
    return datetime.now().astimezone().tzinfo or timezone.utc


def _coerce_now(now: datetime, tz: tzinfo | None) -> datetime:
    effective_tz = _effective_tz(tz)
    if now.tzinfo is None:
        return now.replace(tzinfo=effective_tz)
    return now.astimezone(effective_tz)


def _is_all_day(start: time, end: time) -> bool:
    return start == _MIDNIGHT and end == _MIDNIGHT


def _next_start_after(now: datetime, start: time, end: time) -> datetime:
    candidate_today = _combine(now.date(), start, now.tzinfo)
    if _is_all_day(start, end):
        return candidate_today
    if candidate_today > now:
        return candidate_today
    return _combine(now.date() + timedelta(days=1), start, now.tzinfo)


def _combine(day: date, value: time, tz: tzinfo | None) -> datetime:
    return datetime.combine(day, value, tzinfo=tz)
