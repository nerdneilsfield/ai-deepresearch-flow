from __future__ import annotations

from datetime import datetime, time as time_cls, timezone
import time
from zoneinfo import ZoneInfo

import pytest

from deepresearch_flow.paper.active_window import is_active, next_active_start, parse_windows


def _set_local_tz(monkeypatch: pytest.MonkeyPatch, tz_name: str) -> None:
    monkeypatch.setenv("TZ", tz_name)
    if hasattr(time, "tzset"):
        time.tzset()


def test_parse_windows_accepts_expected_shapes() -> None:
    assert parse_windows([]) == []
    assert parse_windows(["08:00-12:00"]) == [(time_cls(8, 0), time_cls(12, 0))]
    assert parse_windows(["22:00-06:00"]) == [
        (time_cls(22, 0), time_cls(0, 0)),
        (time_cls(0, 0), time_cls(6, 0)),
    ]
    assert parse_windows(["00:00-24:00"]) == [(time_cls(0, 0), time_cls(0, 0))]
    assert parse_windows(["23:00-24:00"]) == [(time_cls(23, 0), time_cls(0, 0))]
    assert parse_windows(["08:00-12:00", "14:00-18:00"]) == [
        (time_cls(8, 0), time_cls(12, 0)),
        (time_cls(14, 0), time_cls(18, 0)),
    ]


@pytest.mark.parametrize(
    "raw",
    [
        ["12:00-12:00"],
        ["24:00-06:00"],
        ["25:00-26:00"],
        ["abc"],
    ],
)
def test_parse_windows_rejects_invalid_values(raw: list[str]) -> None:
    with pytest.raises(ValueError):
        parse_windows(raw)


def test_is_active_respects_boundaries_and_cross_midnight() -> None:
    windows = parse_windows(["08:00-12:00"])

    assert (
        is_active(datetime(2026, 4, 21, 8, 0, tzinfo=timezone.utc), windows, timezone.utc) is True
    )
    assert (
        is_active(datetime(2026, 4, 21, 12, 0, tzinfo=timezone.utc), windows, timezone.utc) is False
    )
    assert (
        is_active(datetime(2026, 4, 21, 7, 59, tzinfo=timezone.utc), windows, timezone.utc) is False
    )

    cross_midnight = parse_windows(["22:00-06:00"])
    assert (
        is_active(datetime(2026, 4, 21, 23, 30, tzinfo=timezone.utc), cross_midnight, timezone.utc)
        is True
    )
    assert (
        is_active(datetime(2026, 4, 21, 3, 0, tzinfo=timezone.utc), cross_midnight, timezone.utc)
        is True
    )

    end_of_day = parse_windows(["23:00-24:00"])
    assert (
        is_active(datetime(2026, 4, 21, 23, 59, tzinfo=timezone.utc), end_of_day, timezone.utc)
        is True
    )

    full_day = parse_windows(["00:00-24:00"])
    assert (
        is_active(datetime(2026, 4, 21, 17, 45, tzinfo=timezone.utc), full_day, timezone.utc)
        is True
    )
    assert is_active(datetime(2026, 4, 21, 17, 45, tzinfo=timezone.utc), [], timezone.utc) is True


def test_is_active_uses_explicit_timezone() -> None:
    windows = parse_windows(["23:00-24:00"])
    now = datetime(2026, 4, 21, 15, 30, tzinfo=timezone.utc)

    assert is_active(now, windows, ZoneInfo("Asia/Shanghai")) is True


def test_is_active_falls_back_to_system_local_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_local_tz(monkeypatch, "Asia/Shanghai")
    windows = parse_windows(["23:00-24:00"])
    now = datetime(2026, 4, 21, 15, 30, tzinfo=timezone.utc)

    assert is_active(now, windows, None) is True


def test_is_active_does_not_fall_back_to_now_tzinfo(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_local_tz(monkeypatch, "UTC")
    windows = parse_windows(["15:00-16:00"])
    now = datetime(2026, 4, 21, 15, 30, tzinfo=ZoneInfo("Asia/Shanghai"))

    assert is_active(now, windows, None) is False


def test_next_active_start_returns_expected_times() -> None:
    windows = parse_windows(["08:00-12:00", "14:00-18:00"])
    active_now = datetime(2026, 4, 21, 9, 30, tzinfo=timezone.utc)
    between_windows = datetime(2026, 4, 21, 12, 1, tzinfo=timezone.utc)
    after_final_window = datetime(2026, 4, 21, 18, 1, tzinfo=timezone.utc)

    assert next_active_start(active_now, windows, timezone.utc) == active_now
    assert next_active_start(between_windows, windows, timezone.utc) == datetime(
        2026, 4, 21, 14, 0, tzinfo=timezone.utc
    )
    assert next_active_start(after_final_window, windows, timezone.utc) == datetime(
        2026, 4, 22, 8, 0, tzinfo=timezone.utc
    )
    assert next_active_start(active_now, [], timezone.utc) is None


def test_active_window_handles_dst_fold_without_wrongly_rejecting_valid_local_time() -> None:
    tz = ZoneInfo("America/New_York")
    windows = parse_windows(["01:00-02:00"])
    first_fold = datetime(2026, 11, 1, 1, 30, tzinfo=tz, fold=0)
    second_fold = datetime(2026, 11, 1, 1, 30, tzinfo=tz, fold=1)

    assert is_active(first_fold, windows, tz) is True
    assert is_active(second_fold, windows, tz) is True


def test_next_active_start_after_forward_clock_jump_returns_future_boundary() -> None:
    tz = ZoneInfo("America/New_York")
    windows = parse_windows(["09:00-10:00"])
    skewed_now = datetime(2026, 4, 21, 23, 30, tzinfo=timezone.utc)

    next_start = next_active_start(skewed_now, windows, tz)

    assert next_start is not None
    assert next_start > skewed_now.astimezone(tz)
