"""Sent is not saved: the bulk PUT answers 200 with no body, so the only proof
that intervals.icu kept a day is reading it back.

The OpenAPI document of intervals.icu (https://intervals.icu/api/v1/docs, read
6 November 2026) documents `PUT /athlete/{id}/wellness-bulk` with a single
response: `200 OK` and **no content schema**. The single-day `PUT
/athlete/{id}/wellness/{date}` does return the record, the bulk one does not.
So a 200 from the bulk endpoint proves the request arrived, nothing else; these
tests pin the read-back that turns "sent" into "saved".
"""

from __future__ import annotations

from datetime import date

import httpx
import pytest
import respx

from conftest import (
    API_KEY,
    INTERVALS_PUT,
    INTERVALS_WELLNESS,
    mock_google,
    mock_refresh,
    store_token,
)
from ghti.cli import main
from ghti.errors import IntervalsError
from ghti.intervals import IntervalsClient
from ghti.mapping import confirm_written

SENT = [
    {"id": "2026-10-20", "steps": 8123, "weight": 72.9},
    {"id": "2026-10-21", "steps": 10456},
]


# --- the comparison itself ---------------------------------------------------


def test_everything_back_means_nothing_to_report():
    found = {"2026-10-20": dict(SENT[0]), "2026-10-21": dict(SENT[1])}
    assert confirm_written(SENT, found) == []


def test_a_missing_day_is_named():
    found = {"2026-10-20": dict(SENT[0])}
    assert confirm_written(SENT, found) == ["2026-10-21 is not on intervals.icu (steps was sent)"]


def test_a_missing_field_is_named_with_the_field():
    found = {"2026-10-20": {"id": "2026-10-20", "steps": 8123}, "2026-10-21": dict(SENT[1])}
    assert confirm_written(SENT, found) == ["2026-10-20: weight was sent but is not saved"]


def test_a_field_saved_as_null_counts_as_not_saved():
    found = {"2026-10-20": {"id": "2026-10-20", "steps": 8123, "weight": None}, "2026-10-21": dict(SENT[1])}
    assert confirm_written(SENT, found) == ["2026-10-20: weight was sent but is not saved"]


def test_a_different_value_is_reported_as_different_not_as_missing():
    found = {"2026-10-20": {"id": "2026-10-20", "steps": 8123, "weight": 70.0}, "2026-10-21": dict(SENT[1])}
    problems = confirm_written(SENT, found)
    assert problems == ["2026-10-20: weight was sent as 72.9 and is saved as 70"]


def test_floats_that_only_differ_in_the_last_bit_are_the_same_value():
    found = {"2026-10-20": {"id": "2026-10-20", "steps": 8123, "weight": 72.9 + 1e-12}, "2026-10-21": dict(SENT[1])}
    assert confirm_written(SENT, found) == []


def test_an_integer_sent_and_read_back_as_a_float_is_the_same_value():
    found = {"2026-10-20": {"id": "2026-10-20", "steps": 8123.0, "weight": 72.9}, "2026-10-21": dict(SENT[1])}
    assert confirm_written(SENT, found) == []


def test_extra_fields_on_the_record_are_not_our_business():
    """intervals.icu computes ctl/atl and keeps the user's own comments: a read-back
    full of fields we never sent is normal, not a problem."""
    found = {
        "2026-10-20": {**SENT[0], "ctl": 42.1, "comments": "easy day", "locked": True},
        "2026-10-21": dict(SENT[1]),
    }
    assert confirm_written(SENT, found) == []


def test_nothing_sent_nothing_to_confirm():
    assert confirm_written([], {}) == []


# --- the read-back request ---------------------------------------------------


def test_read_back_asks_only_for_the_days_and_fields_that_were_sent(mock_http):
    route = mock_http.get(INTERVALS_WELLNESS).mock(return_value=httpx.Response(200, json=[dict(SENT[0])]))
    with httpx.Client() as http:
        client = IntervalsClient(http, API_KEY, "i42")
        found = client.read_back(date(2026, 10, 20), date(2026, 10, 21), {"steps", "weight"})
    params = route.calls[0].request.url.params
    assert params["oldest"] == "2026-10-20"
    assert params["newest"] == "2026-10-21"
    assert params["fields"] == "steps,weight"
    assert set(found) == {"2026-10-20"}


def test_read_back_counts_against_the_request_budget(mock_http):
    mock_http.get(INTERVALS_WELLNESS).mock(return_value=httpx.Response(200, json=[]))
    with httpx.Client() as http:
        client = IntervalsClient(http, API_KEY, "i42")
        client.read_back(date(2026, 10, 20), date(2026, 10, 20), {"steps"})
        assert client.requests == 1


def test_read_back_refused_raises_without_leaking_the_key(mock_http):
    mock_http.get(INTERVALS_WELLNESS).mock(return_value=httpx.Response(403, text="forbidden"))
    with pytest.raises(IntervalsError) as excinfo, httpx.Client() as http:
        IntervalsClient(http, API_KEY, "i42").read_back(date(2026, 10, 20), date(2026, 10, 20), {"steps"})
    assert "INTERVALS_API_KEY" in str(excinfo.value)
    assert API_KEY not in str(excinfo.value)


def test_read_back_that_is_not_a_list_is_an_error_not_a_crash(mock_http):
    mock_http.get(INTERVALS_WELLNESS).mock(return_value=httpx.Response(200, json={"id": "2026-10-20"}))
    with pytest.raises(IntervalsError), httpx.Client() as http:
        IntervalsClient(http, API_KEY, "i42").read_back(date(2026, 10, 20), date(2026, 10, 20), {"steps"})


def test_read_back_ignores_records_without_a_day(mock_http):
    mock_http.get(INTERVALS_WELLNESS).mock(return_value=httpx.Response(200, json=[{"steps": 1}, dict(SENT[1])]))
    with httpx.Client() as http:
        found = IntervalsClient(http, API_KEY, "i42").read_back(date(2026, 10, 21), date(2026, 10, 21), {"steps"})
    assert set(found) == {"2026-10-21"}


# --- inside a whole sync -----------------------------------------------------


def test_sync_says_confirmed_when_everything_comes_back(settings, mock_http, capsys):
    store_token(settings)
    mock_refresh(mock_http)
    mock_google(mock_http)
    mock_http.put(INTERVALS_PUT).mock(return_value=httpx.Response(200))

    assert main(["sync"]) == 0

    out = capsys.readouterr().out
    assert "Sent 3 day(s)" in out
    assert "3 confirmed on intervals.icu" in out
    assert "not saved" not in out


def test_sync_names_the_day_intervals_answered_200_without_saving(settings, mock_http, capsys):
    """The silent case this test exists for: HTTP 200, no error, one day missing."""
    store_token(settings)
    mock_refresh(mock_http)
    mock_google(mock_http)
    mock_http.put(INTERVALS_PUT).mock(return_value=httpx.Response(200))
    mock_http.get(INTERVALS_WELLNESS).mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": "2026-10-20", "steps": 8123, "floorsClimbed": 12, "vo2max": 47.3},
                {"id": "2026-10-22", "sleepSecs": 23400},
            ],
        )
    )

    assert main(["sync"]) == 0

    out = capsys.readouterr().out
    assert "2 confirmed on intervals.icu of 3 sent" in out
    assert "2026-10-21 is not on intervals.icu" in out


def test_a_sync_that_saves_nothing_at_all_is_said_plainly(settings, mock_http, capsys):
    store_token(settings)
    mock_refresh(mock_http)
    mock_google(mock_http)
    mock_http.put(INTERVALS_PUT).mock(return_value=httpx.Response(200))
    mock_http.get(INTERVALS_WELLNESS).mock(return_value=httpx.Response(200, json=[]))

    assert main(["sync"]) == 0

    out = capsys.readouterr().out
    assert "0 confirmed on intervals.icu of 3 sent" in out
    assert "intervals.icu answered 200 but kept none of the 3 day(s)" in out


def test_a_read_back_that_fails_does_not_fail_the_sync_and_promises_nothing(settings, mock_http, capsys):
    """The days were sent; the check could not run. Neither fact is hidden."""
    store_token(settings)
    mock_refresh(mock_http)
    mock_google(mock_http)
    mock_http.put(INTERVALS_PUT).mock(return_value=httpx.Response(200))
    mock_http.get(INTERVALS_WELLNESS).mock(return_value=httpx.Response(500, text="boom"))

    assert main(["sync"]) == 0

    out = capsys.readouterr().out
    assert "Sent 3 day(s)" in out
    assert "not verified" in out
    assert "confirmed on intervals.icu" not in out


def test_no_confirm_skips_the_read_back(settings, mock_http, capsys):
    store_token(settings)
    mock_refresh(mock_http)
    mock_google(mock_http)
    mock_http.put(INTERVALS_PUT).mock(return_value=httpx.Response(200))
    route = mock_http.get(INTERVALS_WELLNESS)

    assert main(["sync", "--no-confirm"]) == 0

    assert route.call_count == 0
    out = capsys.readouterr().out
    assert "Sent 3 day(s)" in out
    assert "confirmed on intervals.icu" not in out


def test_dry_run_never_reads_back(settings, mock_http):
    store_token(settings)
    mock_refresh(mock_http)
    mock_google(mock_http)
    mock_http.put(INTERVALS_PUT).mock(return_value=httpx.Response(200))
    route = mock_http.get(INTERVALS_WELLNESS)

    assert main(["sync", "--dry-run"]) == 0

    assert route.call_count == 0


def test_an_empty_window_costs_no_read_back(settings, mock_http):
    """Nothing was sent, so there is nothing to confirm: no request, no promise."""
    store_token(settings)
    mock_refresh(mock_http)
    empty = {"dataPoints": []}
    mock_google(mock_http, overrides={path: httpx.Response(200, json=empty) for path in _ALL_PATHS()})
    mock_http.put(INTERVALS_PUT).mock(return_value=httpx.Response(200))
    route = mock_http.get(INTERVALS_WELLNESS)

    assert main(["sync"]) == 0

    assert route.call_count == 0


def _ALL_PATHS() -> list[str]:
    from conftest import GOOGLE_ROUTES

    return [path for _, path in GOOGLE_ROUTES]


def test_the_markdown_report_carries_the_confirmation(settings, mock_http, github):
    store_token(settings)
    mock_refresh(mock_http)
    mock_google(mock_http)
    mock_http.put(INTERVALS_PUT).mock(return_value=httpx.Response(200))
    mock_http.get(INTERVALS_WELLNESS).mock(return_value=httpx.Response(200, json=[{"id": "2026-10-20", "steps": 8123}]))

    assert main(["sync"]) == 0

    text = github.read_text()
    assert "1 confirmed on intervals.icu of 3 sent" in text
    assert "2026-10-21 is not on intervals.icu" in text


def test_the_read_back_is_one_request_per_window(settings, mock_http):
    """A year of backfill must not pay a request per day."""
    store_token(settings)
    mock_refresh(mock_http)
    mock_google(mock_http)
    mock_http.put(INTERVALS_PUT).mock(return_value=httpx.Response(200))
    route = mock_http.get(INTERVALS_WELLNESS).mock(return_value=httpx.Response(200, json=[]))

    assert main(["backfill", "--from", "2026-01-01", "--to", "2026-10-22"]) == 0

    assert route.call_count == 1  # only one window had anything to send
    assert respx is not None
