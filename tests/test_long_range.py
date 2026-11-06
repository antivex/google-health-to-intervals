"""The long run: a range longer than one 90-day window.

A backfill of a year is cut into 90-day windows and pushed one window at a
time. Two things can go wrong and nobody would notice:

1. a day on the seam between two windows is asked of nobody, or asked twice;
2. a failure halfway leaves part of the range written and the command still
   says it went well.

Both are measured here against a fake Google that honours the day filter of
each request, exactly as the real one does: a day the windows forget to ask
for is simply never returned, so a gap shows up as a missing record.
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
from ghti.sync import PARTIAL, WINDOW_DAYS, windows

ROME = ZoneInfo("Europe/Rome")
SLEEP_PATH = f"{DT}/sleep/dataPoints:reconcile"
STEPS_PATH = f"{DT}/steps/dataPoints:dailyRollUp"

# conftest.NOW is 2026-10-22 09:00 UTC, so `today` in Europe/Rome is 2026-10-22.
TODAY = date(2026, 10, 22)
LONG_DAYS = 200  # 90 + 90 + 20: two full windows and a short one
FIRST_DAY = TODAY - timedelta(days=LONG_DAYS - 1)


def _day_of(index: int) -> date:
    return FIRST_DAY + timedelta(days=index)


def _night(day: date, minutes: int) -> dict:
    """A night that ends at 07:00 local time on `day`, with that day's real offset."""
    local = datetime(day.year, day.month, day.day, 7, 0, tzinfo=ROME)
    offset = int(local.utcoffset().total_seconds())
    return {
        "dataPointId": f"night-{day.isoformat()}",
        "sleep": {
            "interval": {
                "endTime": local.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                "endUtcOffset": f"{offset}s",
            },
            "metadata": {"nap": False, "mainSleep": True},
            "summary": {"minutesAsleep": str(minutes)},
        },
    }


# One night per day of the whole range, each with its own length, so that a day
# served from the wrong window would show up as a wrong number and not only as
# a missing one.
NIGHTS = {_day_of(i): 360 + (i % 7) * 10 for i in range(LONG_DAYS)}

FILTER = re.compile(r'>= "(\d{4}-\d\d-\d\d)" AND \S+ < "(\d{4}-\d\d-\d\d)"')


def _asked_range(request: httpx.Request) -> tuple[date, date]:
    """The closed-open day range of a filtered GET, as the server reads it."""
    match = FILTER.search(request.url.params["filter"])
    assert match, f"no day filter in {request.url.params.get('filter')!r}"
    return date.fromisoformat(match.group(1)), date.fromisoformat(match.group(2))


def _sleep_server(request: httpx.Request) -> httpx.Response:
    """Answer only with the nights whose wake-up day is inside the asked range."""
    low, high = _asked_range(request)
    points = [_night(day, minutes) for day, minutes in sorted(NIGHTS.items()) if low <= day < high]
    return httpx.Response(200, json={"dataPoints": points})


@pytest.fixture
def long_range(settings, mock_http):
    """A sync of 200 days: Google answers per filter, intervals.icu accepts."""
    store_token(settings)
    mock_refresh(mock_http)
    google = _mock_everything(mock_http)
    put = mock_http.put(INTERVALS_PUT).mock(return_value=httpx.Response(200))
    return google, put


def _mock_everything(mock_http) -> dict:
    """Every Google endpoint empty, except sleep, which honours the day filter."""
    empty = httpx.Response(200, json={"dataPoints": [], "rollupDataPoints": []})
    overrides = {path: empty for _, path in GOOGLE_ROUTES if "sleep" not in path}
    google = mock_google(mock_http, overrides)
    google[SLEEP_PATH] = mock_http.route(method="GET", host=GOOGLE, path=SLEEP_PATH).mock(side_effect=_sleep_server)
    return google


# --- the windows themselves ----------------------------------------------------


def test_the_windows_cover_every_day_exactly_once():
    start, end = FIRST_DAY, TODAY
    cut = list(windows(start, end))
    assert [(a.isoformat(), b.isoformat()) for a, b in cut] == [
        ("2026-04-06", "2026-07-04"),
        ("2026-07-05", "2026-10-02"),
        ("2026-10-03", "2026-10-22"),
    ]
    seen: list[date] = []
    for low, high in cut:
        day = low
        while day <= high:
            seen.append(day)
            day += timedelta(days=1)
    expected = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    assert seen == expected  # in order, no gap, no day twice
    assert all((high - low).days + 1 <= WINDOW_DAYS for low, high in cut)


@pytest.mark.parametrize(
    ("days", "expected"),
    [
        (1, [(0, 0)]),
        (WINDOW_DAYS - 1, [(0, 88)]),
        (WINDOW_DAYS, [(0, 89)]),
        (WINDOW_DAYS + 1, [(0, 89), (90, 90)]),  # the 91st day gets a window of its own
        (2 * WINDOW_DAYS, [(0, 89), (90, 179)]),
    ],
)
def test_the_edges_of_the_cut(days, expected):
    start = date(2026, 1, 1)
    cut = list(windows(start, start + timedelta(days=days - 1)))
    assert [((a - start).days, (b - start).days) for a, b in cut] == expected


# --- the whole run against the fake server -------------------------------------


def test_a_200_day_sync_keeps_every_day(long_range):
    google, put = long_range

    assert main(["sync", "--days", str(LONG_DAYS)]) == 0

    sent = [record for body in put_bodies(put) for record in body]
    assert len(sent) == LONG_DAYS  # no day lost, no day sent twice
    assert [r["id"] for r in sent] == [_day_of(i).isoformat() for i in range(LONG_DAYS)]
    assert {r["sleepSecs"] for r in sent} == {minutes * 60 for minutes in NIGHTS.values()}
    # Each day carries its own night, including the two seams (days 90 and 91,
    # 180 and 181 of the range).
    by_day = {r["id"]: r["sleepSecs"] for r in sent}
    for index in (0, 89, 90, 179, 180, LONG_DAYS - 1):
        day = _day_of(index)
        assert by_day[day.isoformat()] == NIGHTS[day] * 60, f"wrong night on day {index + 1} ({day})"
    assert len(google[SLEEP_PATH].calls) == 3  # one request per window


def test_each_window_is_pushed_on_its_own_so_progress_is_kept(long_range):
    _, put = long_range

    assert main(["sync", "--days", str(LONG_DAYS)]) == 0

    bodies = put_bodies(put)
    assert [len(body) for body in bodies] == [90, 90, 20]


def test_the_day_filters_of_the_windows_touch_without_a_gap(long_range):
    google, _ = long_range

    assert main(["sync", "--days", str(LONG_DAYS)]) == 0

    asked = [_asked_range(call.request) for call in google[SLEEP_PATH].calls]
    assert asked[0][0] == FIRST_DAY
    assert asked[-1][1] == TODAY + timedelta(days=1)  # the last day is included
    for (_, end_exclusive), (next_start, _) in zip(asked, asked[1:], strict=False):
        assert end_exclusive == next_start  # closed-open ranges that meet


def test_the_steps_rollup_asks_contiguous_chunks_of_at_most_90_days(long_range):
    google, _ = long_range

    assert main(["sync", "--days", str(LONG_DAYS)]) == 0

    def day_of(civil: dict) -> date:
        return date(civil["date"]["year"], civil["date"]["month"], civil["date"]["day"])

    ranges = []
    for call in google[STEPS_PATH].calls:
        asked = json.loads(call.request.content)["range"]
        ranges.append((day_of(asked["start"]), day_of(asked["end"])))
    assert ranges[0][0] == FIRST_DAY
    assert ranges[-1][1] == TODAY + timedelta(days=1)
    for (_, end_exclusive), (next_start, _) in zip(ranges, ranges[1:], strict=False):
        assert end_exclusive == next_start
    assert all((end - start).days <= 90 for start, end in ranges)


# --- a failure halfway ---------------------------------------------------------


def test_a_failure_on_the_second_window_keeps_the_first_and_says_so(settings, mock_http, capsys):
    """The run stops, the first window stays written, and the exit code is not 0."""
    store_token(settings)
    mock_refresh(mock_http)
    google = _mock_everything(mock_http)
    answers = [httpx.Response(200), httpx.Response(400, json={"error": "bad day"})]
    put = mock_http.put(INTERVALS_PUT).mock(side_effect=answers)

    code = main(["sync", "--days", str(LONG_DAYS)])

    assert code == PARTIAL  # not 1: the first window is written, and the error says where to carry on
    assert len(put.calls) == 2  # it stopped at the window that failed
    assert [len(body) for body in put_bodies(put)] == [90, 90]
    error = capsys.readouterr().err
    assert "Error:" in error
    assert "intervals.icu" in error.lower()


# --- what the run shows afterwards ---------------------------------------------
# These two guard `report.days`, which is not what gets sent but what the buyer
# reads: the printed report, the GitHub job summary and the CSV. A long range
# fills it one window at a time, so a window that replaces it instead of adding
# to it would leave only the last 20 days, with nothing else to notice it.


def test_the_printed_report_lists_every_day_of_a_long_range(long_range, capsys):
    assert main(["sync", "--days", str(LONG_DAYS)]) == 0

    out = capsys.readouterr().out
    assert f"Sent {LONG_DAYS} day(s)" in out
    listed = [line.split(":")[0].strip() for line in out.splitlines() if line.startswith("  2026-")]
    assert listed == [_day_of(i).isoformat() for i in range(LONG_DAYS)]


def test_a_long_export_writes_every_day_in_the_csv(long_range, capsys):
    assert main(["export-csv", "--days", str(LONG_DAYS)]) == 0

    rows = [line for line in capsys.readouterr().out.strip().splitlines() if line]
    assert rows[0].startswith("date,")
    assert len(rows) - 1 == LONG_DAYS
    assert rows[1].startswith(FIRST_DAY.isoformat())
    assert rows[-1].startswith(TODAY.isoformat())


def test_a_window_of_zero_days_is_refused_instead_of_hanging():
    """Without this belt the loop would never advance: a run would hang, which on
    GitHub Actions means a job that burns its minutes and says nothing."""
    with pytest.raises(ValueError, match="at least one day"):
        list(windows(date(2026, 1, 1), date(2026, 6, 1), size=0))
