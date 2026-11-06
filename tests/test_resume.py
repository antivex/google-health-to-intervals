"""Picking up a long backfill that broke in the middle.

A long range is written one 90-day window at a time, so a failure halfway
leaves the first windows on intervals.icu and the rest missing. The run
already stops with a non-zero exit code (tests/test_long_range.py), but it
used to say nothing about *where* it stopped: the buyer had no way to know
which day was the last one written, so the only safe move was to run the
whole year again and hope the duplicates were harmless.

What is measured here: the day of the break is named, the command to resume
is spelled out, and `ghti status` repeats both the next morning - because a
scheduled run fails while nobody is watching. When nothing was written at
all, no resume is promised: re-running the same command is the right move
and a false `--from` would silently skip real days.
"""

from __future__ import annotations

from datetime import date, timedelta

import httpx
import pytest

from conftest import INTERVALS_PUT, mock_refresh, put_bodies, store_token
from test_long_range import FIRST_DAY, LONG_DAYS, TODAY, _mock_everything

FIRST_WINDOW_END = date(2026, 7, 4)  # 90 days from 2026-04-06
RESUME_FROM = FIRST_WINDOW_END + timedelta(days=1)
RESUME_COMMAND = f"ghti backfill --from {RESUME_FROM} --to {TODAY}"


@pytest.fixture
def breaks_on_window(settings, mock_http):
    """A 200-day sync where intervals.icu refuses the n-th push."""

    def arrange(window: int):
        store_token(settings)
        mock_refresh(mock_http)
        _mock_everything(mock_http)
        answers = [httpx.Response(200)] * (window - 1) + [httpx.Response(400, json={"error": "bad day"})]
        return mock_http.put(INTERVALS_PUT).mock(side_effect=answers)

    return arrange


def test_the_error_names_the_last_written_day_and_the_command_to_resume(breaks_on_window, capsys):
    put = breaks_on_window(2)

    assert main_sync() == 1

    assert [len(body) for body in put_bodies(put)] == [90, 90]  # the second push was refused
    error = capsys.readouterr().err
    assert "intervals.icu" in error.lower()  # the real cause is still the first thing said
    assert FIRST_WINDOW_END.isoformat() in error, error
    assert RESUME_COMMAND in error, error


def test_the_error_counts_the_days_already_on_intervals(breaks_on_window, capsys):
    breaks_on_window(2)

    assert main_sync() == 1

    error = capsys.readouterr().err
    assert "90 day(s)" in error, error
    # Not the whole range: claiming 200 would send the buyer away from the gap.
    assert f"{LONG_DAYS} day(s)" not in error


def test_a_later_break_names_the_later_day(breaks_on_window, capsys):
    """The third window: two windows are written, so the resume day moves on."""
    put = breaks_on_window(3)

    assert main_sync() == 1

    assert [len(body) for body in put_bodies(put)] == [90, 90, 20]
    error = capsys.readouterr().err
    assert "2026-10-02" in error, error  # end of the second window
    assert f"--from 2026-10-03 --to {TODAY}" in error, error
    assert "180 day(s)" in error, error


def test_nothing_written_promises_no_resume(breaks_on_window, capsys):
    """The first window fails: there is no safe `--from`, so none is offered."""
    put = breaks_on_window(1)

    assert main_sync() == 1

    assert put_bodies(put) == [] or [len(b) for b in put_bodies(put)] == [90]
    error = capsys.readouterr().err
    assert "--from" not in error, error
    assert FIRST_DAY.isoformat() not in error.replace(f"{FIRST_DAY} to", ""), error


def test_status_the_next_morning_still_names_the_resume_day(breaks_on_window, capsys):
    """A scheduled run breaks at night; the diary is where the buyer looks."""
    breaks_on_window(2)
    assert main_sync() == 1
    capsys.readouterr()

    from ghti.cli import main

    assert main(["status"]) == 0

    out = capsys.readouterr().out
    assert "FAILED" in out, out
    assert RESUME_COMMAND in out, out


def test_a_dry_run_never_promises_a_resume(settings, mock_http, capsys):
    """`--dry-run` writes nothing, so there is nothing to resume from. The only
    way a dry run fails mid-range is Google refusing every metric."""
    store_token(settings)
    mock_refresh(mock_http)
    refused = httpx.Response(403, json={"error": {"message": "no"}})
    from conftest import GOOGLE_ROUTES, mock_google

    mock_google(mock_http, {path: refused for _, path in GOOGLE_ROUTES})

    code = main_sync(dry_run=True)

    assert code == 1
    error = capsys.readouterr().err
    assert "--from" not in error, error


def main_sync(dry_run: bool = False) -> int:
    from ghti.cli import main

    argv = ["sync", "--days", str(LONG_DAYS)]
    if dry_run:
        argv.append("--dry-run")
    return main(argv)


# A dropped connection is the likeliest break of all: it is not one of our own
# errors, so it travels a different path out of the sync and needs its own test.


def test_a_dropped_connection_halfway_also_says_where_to_carry_on(settings, mock_http, capsys):
    store_token(settings)
    mock_refresh(mock_http)
    _mock_everything(mock_http)
    mock_http.put(INTERVALS_PUT).mock(side_effect=[httpx.Response(200), httpx.ConnectError("connection lost")])

    assert main_sync() == 1

    error = capsys.readouterr().err
    assert "ConnectError" in error, error  # the cause is still named
    assert RESUME_COMMAND in error, error


def test_a_dry_run_that_drops_the_connection_promises_no_resume(settings, mock_http, capsys):
    """A dry run reads 90 days fine and then loses the line. Nothing was ever
    written, so a `--from` here would tell the buyer to skip 90 real days."""
    from conftest import DT, GOOGLE, GOOGLE_ROUTES, mock_google

    store_token(settings)
    mock_refresh(mock_http)
    empty = httpx.Response(200, json={"dataPoints": [], "rollupDataPoints": []})
    mock_google(mock_http, {path: empty for _, path in GOOGLE_ROUTES})
    sleep_path = f"{DT}/sleep/dataPoints:reconcile"
    mock_http.route(method="GET", host=GOOGLE, path=sleep_path).mock(
        side_effect=[empty, httpx.ConnectError("connection lost")]
    )

    assert main_sync(dry_run=True) == 1

    error = capsys.readouterr().err
    assert "ConnectError" in error, error
    assert "--from" not in error, error
    assert "already on intervals.icu" not in error, error


def test_empty_days_still_count_as_done(settings, mock_http, capsys):
    """A year-long backfill often starts before the watch existed: the first
    window is read, found empty and sent to nobody. It is still done, so the
    resume day must move past it instead of starting the range again."""
    from conftest import DT, GOOGLE, GOOGLE_ROUTES, mock_google

    store_token(settings)
    mock_refresh(mock_http)
    empty = httpx.Response(200, json={"dataPoints": [], "rollupDataPoints": []})
    mock_google(mock_http, {path: empty for _, path in GOOGLE_ROUTES})
    sleep_path = f"{DT}/sleep/dataPoints:reconcile"
    mock_http.route(method="GET", host=GOOGLE, path=sleep_path).mock(
        side_effect=[empty, httpx.ConnectError("connection lost")]
    )
    put = mock_http.put(INTERVALS_PUT).mock(return_value=httpx.Response(200))

    assert main_sync() == 1

    assert put.calls == []  # nothing was written: there was nothing to write
    error = capsys.readouterr().err
    assert "0 day(s)" not in error, error  # that would read like a run that did nothing
    assert "no data to send" in error, error
    assert RESUME_COMMAND in error, error
