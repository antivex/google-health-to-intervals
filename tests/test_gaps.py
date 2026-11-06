"""`ghti gaps`: what intervals.icu really holds, not what the diary believes.

Measured on 6 November 2026, before this file existed. A 200-day backfill died
after 90 days, so the diary recorded a gap of 2026-07-05..2026-10-22. The user
then finished most of it by other means, up to 30 September. `ghti status`
kept printing all 110 days as missing and
`ghti backfill --from 2026-07-05 --to 2026-10-22` as the cure, because the
diary only knows what this computer tried to send. 88 of those days were
already there; the 22 that were really missing were never named on their own.
And no command existed to ask intervals.icu anything: the sub-commands were
auth-finish, auth-url, backfill, check, demo, export-csv, schedule, status,
sync.

Asking is cheap, and that was the other half of the measurement: the client
reads 100 days per request, so a month costs 1 request and a whole year 4,
against the 5,000 a day intervals.icu allows.

What `gaps` cannot know is stated in its own output: a day the watch never
recorded looks exactly like a day that was lost.
"""

from __future__ import annotations

from datetime import date, timedelta

import httpx
import pytest

from conftest import API_KEY, INTERVALS_WELLNESS
from ghti import gaps
from ghti.cli import build_parser, main
from ghti.intervals import CHUNK_SIZE, IntervalsClient
from test_unfinished import broke_halfway  # noqa: F401

DAY = timedelta(days=1)


@pytest.fixture
def client():
    """A real httpx client; respx (autouse in conftest) answers for it."""
    with httpx.Client() as http:
        yield IntervalsClient(http, API_KEY, "i42")


def days(first: date, last: date) -> list[date]:
    out, day = [], first
    while day <= last:
        out.append(day)
        day += DAY
    return out


def serve(mock_http, present: list[date]):
    """An intervals.icu that holds exactly these days, honouring oldest/newest."""
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        calls.append(dict(params))
        oldest = date.fromisoformat(params["oldest"])
        newest = date.fromisoformat(params["newest"])
        body = [{"id": d.isoformat(), "steps": 1000} for d in present if oldest <= d <= newest]
        return httpx.Response(200, json=body)

    mock_http.get(INTERVALS_WELLNESS).mock(side_effect=handler)
    return calls


# --- the shapes --------------------------------------------------------------


def test_as_ranges_joins_consecutive_days_and_keeps_singles():
    d = date(2026, 10, 1)
    assert gaps.as_ranges([d, d + DAY, d + 2 * DAY, d + 5 * DAY]) == [(d, d + 2 * DAY), (d + 5 * DAY, d + 5 * DAY)]


def test_as_ranges_sorts_and_handles_nothing():
    d = date(2026, 10, 1)
    assert gaps.as_ranges([]) == []
    assert gaps.as_ranges([d + DAY, d]) == [(d, d + DAY)]


@pytest.mark.parametrize(
    "span,expected",
    [(1, 1), (30, 1), (CHUNK_SIZE, 1), (CHUNK_SIZE + 1, 2), (365, 4)],
)
def test_requests_for_counts_the_price_of_asking(span, expected):
    start = date(2026, 1, 1)
    assert gaps.requests_for(start, start + timedelta(days=span - 1)) == expected


# --- reading intervals.icu ---------------------------------------------------


def test_missing_names_only_the_days_intervals_icu_has_nothing_on(mock_http, client):
    start, end = date(2026, 7, 5), date(2026, 10, 22)
    serve(mock_http, days(start, date(2026, 9, 30)))
    assert gaps.missing(client, start, end) == [(date(2026, 10, 1), end)]


def test_missing_is_empty_when_every_day_is_there(mock_http, client):
    start, end = date(2026, 10, 1), date(2026, 10, 10)
    serve(mock_http, days(start, end))
    assert gaps.missing(client, start, end) == []


def test_missing_reports_the_whole_range_when_nothing_is_there(mock_http, client):
    start, end = date(2026, 10, 1), date(2026, 10, 10)
    serve(mock_http, [])
    assert gaps.missing(client, start, end) == [(start, end)]


def test_a_long_range_is_read_in_chunks_of_the_client_size(mock_http, client):
    start, end = date(2026, 1, 1), date(2026, 9, 7)  # 250 days
    calls = serve(mock_http, days(start, end))
    gaps.missing(client, start, end)
    assert len(calls) == 3
    assert calls[0]["oldest"] == "2026-01-01"
    assert calls[-1]["newest"] == "2026-09-07"


def test_the_chunks_cover_the_range_once_with_no_hole_and_no_overlap(mock_http, client):
    start, end = date(2026, 1, 1), date(2026, 9, 7)
    calls = serve(mock_http, [])
    gaps.missing(client, start, end)
    seen = []
    for call in calls:
        seen += days(date.fromisoformat(call["oldest"]), date.fromisoformat(call["newest"]))
    assert seen == days(start, end)


def test_only_the_fields_we_write_are_asked_for_so_nulls_are_dropped(mock_http, client):
    start = date(2026, 10, 1)
    calls = serve(mock_http, [])
    gaps.missing(client, start, start)
    asked = calls[0]["fields"].split(",")
    assert "steps" in asked and "sleepSecs" in asked
    assert "id" not in asked


def test_a_day_outside_the_range_does_not_fill_a_day_inside_it(mock_http, client):
    """A server that ignores oldest/newest must not make a missing day disappear."""
    start, end = date(2026, 10, 1), date(2026, 10, 3)
    mock_http.get(INTERVALS_WELLNESS).mock(
        return_value=httpx.Response(200, json=[{"id": "2026-11-15", "steps": 1}])
    )
    assert gaps.missing(client, start, end) == [(start, end)]


def test_a_key_that_is_not_a_date_is_ignored_instead_of_crashing(mock_http, client):
    start, end = date(2026, 10, 1), date(2026, 10, 2)
    mock_http.get(INTERVALS_WELLNESS).mock(
        return_value=httpx.Response(200, json=[{"id": "not-a-day"}, {"id": "2026-10-01", "steps": 1}])
    )
    assert gaps.missing(client, start, end) == [(end, end)]


# --- what it says ------------------------------------------------------------


def test_text_names_each_range_with_the_command_that_fills_just_it():
    start, end = date(2026, 7, 5), date(2026, 10, 22)
    out = gaps.text([(date(2026, 10, 1), end)], start, end)
    assert "22 of the 110 day(s)" in out
    assert "ghti backfill --from 2026-10-01 --to 2026-10-22" in out
    assert "ghti backfill --from 2026-07-05" not in out  # the diary's over-wide command is gone


def test_text_says_so_plainly_when_nothing_is_missing():
    start, end = date(2026, 10, 1), date(2026, 10, 10)
    out = gaps.text([], start, end)
    assert "Every day" in out and "10 day(s)" in out
    assert "backfill" not in out


def test_text_warns_that_an_empty_day_may_just_be_a_day_without_the_watch():
    start = date(2026, 10, 1)
    assert "did not wear the watch" in gaps.text([(start, start)], start, start)


def test_a_single_missing_day_is_not_written_as_a_range():
    start, end = date(2026, 10, 1), date(2026, 10, 3)
    out = gaps.text([(end, end)], start, end)
    assert "  2026-10-03 (1 day(s))" in out


# --- the command -------------------------------------------------------------


def test_gaps_is_a_command_that_writes_nothing(settings, mock_http, capsys):
    assert "gaps" in build_parser()._subparsers._group_actions[0].choices  # type: ignore[attr-defined]
    serve(mock_http, days(date(2026, 10, 1), date(2026, 10, 10)))
    assert main(["gaps", "--from", "2026-10-01", "--to", "2026-10-10"]) == 0
    assert not mock_http.calls or all(call.request.method == "GET" for call in mock_http.calls)
    assert "Every day" in capsys.readouterr().out


def test_the_command_exits_1_when_days_are_missing(settings, mock_http, capsys):
    serve(mock_http, [])
    assert main(["gaps", "--from", "2026-10-01", "--to", "2026-10-03"]) == 1
    assert "2026-10-01 to 2026-10-03" in capsys.readouterr().out


def test_the_command_says_what_the_question_costs(settings, mock_http, capsys):
    serve(mock_http, [])
    main(["gaps", "--from", "2026-01-01", "--to", "2026-12-31"])
    assert "365 day(s) back from intervals.icu (4 request(s))" in capsys.readouterr().out


def test_from_after_to_is_refused(settings, mock_http, capsys):
    assert main(["gaps", "--from", "2026-10-10", "--to", "2026-10-01"]) == 2
    assert "is after" in capsys.readouterr().err


# --- the measured case, end to end -------------------------------------------


def test_the_diary_gap_is_rechecked_and_shrinks_to_what_is_really_missing(
    settings, mock_http, capsys, broke_halfway
):
    """The whole point: 110 days claimed by the diary, 22 really missing."""
    broke_halfway()
    serve(mock_http, days(date(2026, 7, 5), date(2026, 9, 30)))
    assert main(["gaps"]) == 1
    out = capsys.readouterr().out
    assert "Checking the range the diary says is unfinished: 2026-07-05 to 2026-10-22." in out
    assert "22 of the 110 day(s)" in out
    assert "ghti backfill --from 2026-10-01 --to 2026-10-22" in out


def test_with_no_diary_gap_it_checks_the_last_days_asked_for(settings, mock_http, capsys):
    calls = serve(mock_http, [])
    main(["gaps", "--days", "7"])
    assert calls[0]["oldest"] == "2026-10-16"  # settings.today() is 2026-10-22 in the tests
    assert calls[0]["newest"] == "2026-10-22"


# --- the windows themselves --------------------------------------------------


def test_the_last_window_is_a_single_day_and_the_walk_still_ends():
    """The hang this file found: a window that restarted on its own last day.

    `windows` used to advance to `stop` instead of the day after it. With a
    range whose final window is one day long (2026-07-05..2026-10-22 is such a
    range: 100 + 10 days) it yielded that day for ever and the whole test
    suite hung rather than failing. Returning a list instead of yielding keeps
    the mistake visible.
    """
    start, end = date(2026, 7, 5), date(2026, 10, 22)
    got = gaps.windows(start, end)
    assert got[0] == (start, start + timedelta(days=CHUNK_SIZE - 1))
    assert got[-1][1] == end
    assert len(got) == gaps.requests_for(start, end)
    for before, after in zip(got, got[1:]):
        assert after[0] == before[1] + DAY  # no hole, no overlap


def test_a_range_of_one_day_is_one_window():
    start = date(2026, 10, 1)
    assert gaps.windows(start, start) == [(start, start)]
