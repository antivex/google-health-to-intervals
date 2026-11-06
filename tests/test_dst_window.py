"""Backfill across the two nights the clock moves: a whole window, not one night.

`tests/test_time.py` measures a three-day `sync` over the end of daylight saving
time. What was never measured is a **backfill**: a long range cut into 90-day
windows, with the 25-hour day and the 23-hour day inside those windows and,
worse, exactly on the seam between two of them.

Two different things can drift and nobody would notice:

1. the **cut**: the windows are built with calendar arithmetic, so in theory a
   day of 23 or 25 hours changes nothing - but that was an argument, not a
   measurement, and a day on the seam is asked of nobody or asked twice;
2. the **filing**: 01:30 local happens twice on the last Sunday of October and
   never on the last Sunday of March, so a night that ends in that hour can be
   filed under the wrong day, inside the right window.

The fake Google here honours the day filter of every request, as the real one
does: a day no window asks for is simply never returned, so a gap shows up as a
missing record instead of being papered over by the mock.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest

from conftest import DT, GOOGLE, GOOGLE_ROUTES, INTERVALS_PUT, mock_google, mock_refresh, put_bodies, store_token
from ghti.cli import main
from ghti.mapping import _local_day
from ghti.sync import windows

ROME = ZoneInfo("Europe/Rome")
SLEEP_PATH = f"{DT}/sleep/dataPoints:reconcile"
STEPS_PATH = f"{DT}/steps/dataPoints:dailyRollUp"

# Europe/Rome, measured below and not assumed: the clock goes back on Sunday
# 26 October 2025 (that day lasts 25 hours) and forward on Sunday 29 March 2026
# (23 hours). conftest.NOW is 2026-10-22, so both are in the past.
FALL_BACK = date(2025, 10, 26)  # 25 hours
SPRING_FORWARD = date(2026, 3, 29)  # 23 hours

# A range chosen so that the 23-hour day is the LAST day of the second window
# (the seam falls on it) and the 25-hour day sits in the middle of the first.
SEAM_ON_SPRING = (date(2025, 10, 1), date(2026, 4, 30))
# A shorter range whose first window ENDS on the 25-hour day.
SEAM_ON_FALL = (date(2025, 7, 29), date(2025, 12, 31))

FILTER = re.compile(r'>= "(\d{4}-\d\d-\d\d)" AND \S+ < "(\d{4}-\d\d-\d\d)"')


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def _minutes(day: date) -> int:
    """A different night length per day, so a day served from the wrong window
    shows up as a wrong number and not only as a missing one."""
    return 300 + (day.toordinal() % 11) * 10


def _night(day: date, minutes: int, hour: int = 7, offset_seconds: int | None = None) -> dict:
    """A night that ends at `hour` local time on `day`.

    With no `offset_seconds` the real Rome offset of that instant is used, which
    is what a watch sends. Passing one explicitly is how the two impossible
    hours are built: 01:30 twice in October, 02:30 never in March.
    """
    local = datetime(day.year, day.month, day.day, hour, 30, tzinfo=ROME)
    offset = offset_seconds if offset_seconds is not None else int(local.utcoffset().total_seconds())
    instant = local.astimezone(UTC) if offset_seconds is None else local.replace(tzinfo=UTC) - timedelta(seconds=offset)
    return {
        "dataPointId": f"night-{day.isoformat()}-{hour}",
        "sleep": {
            "interval": {
                "endTime": instant.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                "endUtcOffset": f"{offset}s",
            },
            "metadata": {"nap": False, "mainSleep": True},
            "summary": {"minutesAsleep": str(minutes)},
        },
    }


def _asked_range(request: httpx.Request) -> tuple[date, date]:
    match = FILTER.search(request.url.params["filter"])
    assert match, f"no day filter in {request.url.params.get('filter')!r}"
    return date.fromisoformat(match.group(1)), date.fromisoformat(match.group(2))


def _server(nights: dict[date, int]):
    """Answer only with the nights whose wake-up day is inside the asked range."""

    def handler(request: httpx.Request) -> httpx.Response:
        low, high = _asked_range(request)
        points = [_night(day, minutes) for day, minutes in sorted(nights.items()) if low <= day < high]
        return httpx.Response(200, json={"dataPoints": points})

    return handler


def _mock(mock_http, handler) -> dict:
    empty = httpx.Response(200, json={"dataPoints": [], "rollupDataPoints": []})
    overrides = {path: empty for _, path in GOOGLE_ROUTES if "sleep" not in path}
    google = mock_google(mock_http, overrides)
    google[SLEEP_PATH] = mock_http.route(method="GET", host=GOOGLE, path=SLEEP_PATH).mock(side_effect=handler)
    return google


@pytest.fixture
def run(settings, mock_http):
    """Run a backfill over `span`; returns the Google routes and the PUT route."""
    store_token(settings)
    mock_refresh(mock_http)

    def go(span: tuple[date, date], nights: dict[date, int] | None = None):
        start, end = span
        nights = {day: _minutes(day) for day in _days(start, end)} if nights is None else nights
        google = _mock(mock_http, _server(nights))
        put = mock_http.put(INTERVALS_PUT).mock(return_value=httpx.Response(200))
        assert main(["backfill", "--from", start.isoformat(), "--to", end.isoformat()]) == 0
        return google, put, nights

    return go


# --- the facts the rest of the file is built on --------------------------------


def test_the_zone_database_on_this_machine_has_the_two_switches():
    """Measured, not assumed: 26 October 2025 lasts 25 hours in Rome and
    29 March 2026 lasts 23. If a future tzdata update moved them, this test
    fails first and says so, instead of the others failing for the wrong reason."""

    def hours(day: date) -> float:
        # Converted to UTC on purpose: adding a timedelta to an aware datetime
        # keeps the same wall clock and the same zone, so the subtraction would
        # say 24 hours for every day of the year.
        begin = datetime(day.year, day.month, day.day, tzinfo=ROME).astimezone(UTC)
        end = (datetime(day.year, day.month, day.day, tzinfo=ROME) + timedelta(days=1)).astimezone(UTC)
        return (end - begin).total_seconds() / 3600

    assert hours(FALL_BACK) == 25
    assert hours(SPRING_FORWARD) == 23
    assert hours(date(2025, 6, 1)) == 24  # an ordinary day, as a control


# --- the cut --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("span", "expected_seams", "switch"),
    [
        (SEAM_ON_SPRING, [(date(2025, 12, 29), date(2025, 12, 30)), (SPRING_FORWARD, date(2026, 3, 30))], SPRING_FORWARD),
        (SEAM_ON_FALL, [(FALL_BACK, date(2025, 10, 27))], FALL_BACK),
    ],
    ids=["seam-on-the-23-hour-day", "seam-on-the-25-hour-day"],
)
def test_the_switch_day_lands_on_a_seam_and_the_cut_still_covers_every_day(span, expected_seams, switch):
    start, end = span
    cut = list(windows(start, end))
    seams = [(high, next_low) for (_, high), (next_low, _) in zip(cut, cut[1:], strict=False)]
    assert seams == expected_seams, "the range no longer puts a window seam where this test needs one"
    assert switch in [day for low, high in cut for day in _days(low, high)]

    seen = [day for low, high in cut for day in _days(low, high)]
    assert seen == _days(start, end)  # in order, no gap, no day twice


# --- the whole run against the fake server --------------------------------------


@pytest.mark.parametrize("span", [SEAM_ON_SPRING, SEAM_ON_FALL], ids=["spring", "fall"])
def test_a_backfill_over_a_clock_change_keeps_every_day_exactly_once(run, span):
    _, put, nights = run(span)

    sent = [record for body in put_bodies(put) for record in body]
    assert [r["id"] for r in sent] == [day.isoformat() for day in _days(*span)]
    assert {r["id"] for r in sent} == {day.isoformat() for day in nights}  # none lost, none twice
    by_day = {r["id"]: r["sleepSecs"] for r in sent}
    for day, minutes in nights.items():
        assert by_day[day.isoformat()] == minutes * 60, f"wrong night on {day}"


@pytest.mark.parametrize(
    ("span", "switch"), [(SEAM_ON_SPRING, SPRING_FORWARD), (SEAM_ON_FALL, FALL_BACK)], ids=["spring", "fall"]
)
def test_the_switch_day_is_asked_for_by_exactly_one_window(run, span, switch):
    google, _, _ = run(span)

    asked = [_asked_range(call.request) for call in google[SLEEP_PATH].calls]
    covering = [(low, high) for low, high in asked if low <= switch < high]
    assert len(covering) == 1, f"{switch} is asked for by {len(covering)} windows, not one"
    for (_, end_exclusive), (next_start, _) in zip(asked, asked[1:], strict=False):
        assert end_exclusive == next_start  # closed-open ranges that meet


@pytest.mark.parametrize(
    ("span", "switch"), [(SEAM_ON_SPRING, SPRING_FORWARD), (SEAM_ON_FALL, FALL_BACK)], ids=["spring", "fall"]
)
def test_the_steps_rollup_covers_the_switch_day_once_with_civil_midnights(run, span, switch):
    """The steps endpoint is asked with CivilDateTime, i.e. local midnight, and
    `windowSizeDays: 1`. A 23- or 25-hour day must still be one window, and the
    chunks must meet without a gap across the switch."""
    google, _, _ = run(span)

    def day_of(civil: dict) -> date:
        assert civil["time"] == {"hours": 0, "minutes": 0, "seconds": 0, "nanos": 0}
        return date(civil["date"]["year"], civil["date"]["month"], civil["date"]["day"])

    ranges = []
    for call in google[STEPS_PATH].calls:
        body = json.loads(call.request.content)
        assert body["windowSizeDays"] == 1
        ranges.append((day_of(body["range"]["start"]), day_of(body["range"]["end"])))

    assert ranges[0][0] == span[0]
    assert ranges[-1][1] == span[1] + timedelta(days=1)
    for (_, end_exclusive), (next_start, _) in zip(ranges, ranges[1:], strict=False):
        assert end_exclusive == next_start
    assert len([1 for low, high in ranges if low <= switch < high]) == 1


# --- the filing: the hour that happens twice and the hour that never happens ----


@pytest.fixture
def run_points(settings, mock_http):
    """Run a backfill over `span` with an explicit list of sleep points, each
    kept only if its wake-up day falls inside the asked range."""
    store_token(settings)
    mock_refresh(mock_http)

    def go(span: tuple[date, date], points: list[dict]):
        def handler(request: httpx.Request) -> httpx.Response:
            low, high = _asked_range(request)
            kept = [p for p in points if (day := _wake_day(p)) is not None and low <= day < high]
            return httpx.Response(200, json={"dataPoints": kept})

        _mock(mock_http, handler)
        put = mock_http.put(INTERVALS_PUT).mock(return_value=httpx.Response(200))
        assert main(["backfill", "--from", span[0].isoformat(), "--to", span[1].isoformat()]) == 0
        return put

    return go


def _wake_day(point: dict) -> date | None:
    interval = point["sleep"]["interval"]
    return _local_day(interval["endTime"], interval["endUtcOffset"])


def test_the_hour_lived_twice_in_october_stays_on_its_own_day(run_points):
    """01:30 on 26 October 2025 happens twice in Rome: once as CEST (+2, i.e.
    23:30Z on the 25th) and once as CET (+1, i.e. 00:30Z on the 26th). Both are
    the same calendar day for the owner, so the two sleeps are added up into ONE
    record for the 26th: a clock change must not split a day into two rows, nor
    push half of it onto the 25th."""
    before = date(2025, 10, 25)
    points = [
        _night(before, 400),  # ordinary night, woke 07:30 CEST on the 25th
        _night(FALL_BACK, 90, hour=1, offset_seconds=7200),  # 01:30 CEST, the first time
        _night(FALL_BACK, 30, hour=1, offset_seconds=3600),  # 01:30 CET, the second time
    ]

    put = run_points((before, date(2025, 10, 27)), points)

    assert put_bodies(put) == [
        [
            {"id": "2025-10-25", "sleepSecs": 400 * 60},
            {"id": "2025-10-26", "sleepSecs": (90 + 30) * 60},
        ]
    ]


def test_the_late_evening_before_the_fall_back_is_not_pulled_into_the_next_day(run_points):
    """23:30 CEST on 25 October 2025 is 21:30Z: in UTC it is still the 25th, but
    the first repeated hour of the night after it is already 23:30Z *of the 25th*.
    Reading those two in UTC would put them on the same day; in Rome they are two."""
    points = [
        _night(date(2025, 10, 25), 50, hour=23, offset_seconds=7200),  # 23:30 CEST -> 25 Oct
        _night(FALL_BACK, 70, hour=1, offset_seconds=7200),  # 01:30 CEST -> 26 Oct, 23:30Z of the 25th
    ]

    put = run_points((date(2025, 10, 25), FALL_BACK), points)

    assert put_bodies(put) == [
        [{"id": "2025-10-25", "sleepSecs": 50 * 60}, {"id": "2025-10-26", "sleepSecs": 70 * 60}]
    ]


def test_the_hour_that_never_happened_in_march_does_not_lose_its_day(run_points):
    """On 29 March 2026 the local hour 02:00-03:00 does not exist in Rome. A
    watch that woke the owner at 01:30 CET (00:30Z) and one that woke them at
    03:30 CEST (01:30Z) are one hour apart in real time and both on the 29th:
    neither may slide onto the 28th, and the 23-hour day must not come out empty."""
    points = [
        _night(SPRING_FORWARD, 60, hour=1, offset_seconds=3600),  # 01:30 CET, before the jump
        _night(SPRING_FORWARD, 45, hour=3, offset_seconds=7200),  # 03:30 CEST, right after it
        _night(date(2026, 3, 30), 300),  # the day after, so a slide forward would show too
    ]

    put = run_points((date(2026, 3, 28), date(2026, 3, 30)), points)

    assert put_bodies(put) == [
        [
            {"id": "2026-03-29", "sleepSecs": (60 + 45) * 60},
            {"id": "2026-03-30", "sleepSecs": 300 * 60},
        ]
    ]
