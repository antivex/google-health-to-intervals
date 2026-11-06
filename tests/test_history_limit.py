"""A range older than the data Google keeps for you.

Google keeps a user's data from the day it was recorded and puts no limit on
how far back an application may ask (data-types docs, read 6 November 2026), so
`ghti backfill --from 2019-01-01` is a legal request that simply reads empty
days: Google answers 200 with no data points, not an error.

What the command did with that answer was the problem: it printed
`Sent 0 day(s)` and stopped there. The same line comes out of a broken
authorization, a wrong athlete id or a watch that was never worn, so the one
person who cannot tell the difference is the one who paid for the guide.

These tests measure the answer, not the plumbing: for an empty range the
command must say the days are empty on Google and that nothing here is broken,
and for a range that only starts part-way it must say which day the history
starts on. Both must also reach the GitHub job summary, because a scheduled
run has nobody watching the screen.
"""

from __future__ import annotations

from datetime import date, timedelta

import httpx
import pytest

from conftest import DT, GOOGLE_ROUTES, INTERVALS_PUT, NOW, REFRESH_TOKEN, keys, mock_google, mock_refresh, store_token
from ghti.cli import main
from ghti.oauth import StoredToken, TokenStore

# A period long before the user owned the watch (conftest.NOW is 22/10/2026).
OLD_FROM = date(2019, 1, 1)
OLD_TO = date(2019, 3, 31)

EMPTY = {"dataPoints": [], "rollupDataPoints": []}


def _all_empty() -> dict:
    """Every Google endpoint answers 200 with no data points: nothing recorded."""
    return {path: httpx.Response(200, json=EMPTY) for _, path in GOOGLE_ROUTES}


def _steps_from(first: date, last: date) -> dict:
    """Daily steps from `first` to `last` only: the history starts at `first`."""
    day = first
    points = []
    while day <= last:
        nxt = day + timedelta(days=1)
        points.append(
            {
                "civilStartTime": {"date": {"year": day.year, "month": day.month, "day": day.day}},
                "civilEndTime": {"date": {"year": nxt.year, "month": nxt.month, "day": nxt.day}},
                "steps": {"countSum": "4200"},
            }
        )
        day += timedelta(days=1)
    return {"rollupDataPoints": points}


def _store_token_with_scopes(settings, scopes: list[str]) -> None:
    """A stored authorization that granted only `scopes` (store_token grants all)."""
    obtained = NOW - timedelta(days=1)
    token = StoredToken(REFRESH_TOKEN, obtained, obtained + timedelta(days=7), scopes, expiry_known=True)
    TokenStore(settings.token_file, keys()).save(token)


@pytest.fixture
def google_empty(mock_http, settings):
    """An authorized setup where Google has no data for the asked days."""
    store_token(settings)
    mock_refresh(mock_http)
    mock_google(mock_http, _all_empty())
    return mock_http.put(INTERVALS_PUT).mock(return_value=httpx.Response(200, json={}))


def _run(capsys, *args: str) -> tuple[int, str]:
    code = main(list(args))
    return code, capsys.readouterr().out


def test_empty_range_says_the_days_are_empty_on_google(google_empty, capsys):
    code, out = _run(capsys, "backfill", "--from", OLD_FROM.isoformat(), "--to", OLD_TO.isoformat())

    assert code == 0, "an empty period is not a failure: there is nothing to retry"
    assert "no data at all" in out.lower(), out
    assert str(OLD_FROM) in out and str(OLD_TO) in out, out
    assert "recorded" in out.lower(), "must say the history starts when the data was recorded"
    assert not google_empty.calls, "nothing to send, so intervals.icu must not be called"


def test_empty_range_does_not_look_like_a_broken_bridge(google_empty, capsys):
    """The old output was `Sent 0 day(s)` and nothing else: it read as a fault."""
    _, out = _run(capsys, "backfill", "--from", OLD_FROM.isoformat(), "--to", OLD_TO.isoformat())

    assert out.strip() != f"Sent 0 day(s) to intervals.icu ({OLD_FROM} to {OLD_TO})."
    assert "nothing is broken" in out.lower(), out


def test_empty_range_reaches_the_github_summary(google_empty, github, capsys):
    """A scheduled backfill has nobody at the screen: the summary must carry it."""
    _run(capsys, "backfill", "--from", OLD_FROM.isoformat(), "--to", OLD_TO.isoformat())

    assert "no data at all" in github.read_text().lower(), github.read_text()


def test_history_that_starts_part_way_says_the_first_day(mock_http, settings, capsys):
    """Only the last ten days of a 90-day range exist: say where the history starts."""
    start = date(2026, 7, 25)
    end = date(2026, 10, 22)
    first = end - timedelta(days=9)
    store_token(settings)
    mock_refresh(mock_http)
    overrides = _all_empty()
    overrides[f"{DT}/steps/dataPoints:dailyRollUp"] = httpx.Response(200, json=_steps_from(first, end))
    mock_google(mock_http, overrides)
    mock_http.put(INTERVALS_PUT).mock(return_value=httpx.Response(200, json={}))

    code, out = _run(capsys, "backfill", "--from", start.isoformat(), "--to", end.isoformat())

    assert code == 0
    assert f"nothing before {first}" in out.lower(), out
    assert str(start) in out, "must name the empty stretch that was asked for"
    assert "no data at all" not in out.lower(), "there is data: do not say the range is empty"


def test_a_full_range_says_nothing_about_history(mock_http, settings, capsys):
    """The normal case must stay quiet: no warning when every day has data."""
    store_token(settings)
    mock_refresh(mock_http)
    mock_google(mock_http)
    mock_http.put(INTERVALS_PUT).mock(return_value=httpx.Response(200, json={}))

    code, out = _run(capsys, "sync", "--days", "3")

    assert code == 0
    assert "no data at all" not in out.lower(), out
    assert "nothing before" not in out.lower(), out


def test_no_permission_is_not_reported_as_an_empty_history(mock_http, settings, capsys):
    """With no scope granted nothing is read at all: that is a permission problem,
    and calling it an empty history would send the user looking at the wrong thing."""
    _store_token_with_scopes(settings, ["openid"])  # authorized, but not for any health data
    mock_refresh(mock_http)
    mock_google(mock_http, _all_empty())

    code, out = _run(capsys, "backfill", "--from", OLD_FROM.isoformat(), "--to", OLD_TO.isoformat())

    assert code == 0
    assert "no data at all" not in out.lower(), out
    assert "permission not granted" in out, out
