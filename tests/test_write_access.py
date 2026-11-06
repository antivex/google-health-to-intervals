"""A key that can read is not a key that can write.

`ghti check` used to ask intervals.icu for one day of wellness (a GET) and then
say "Data will be written to athlete X". A read-only key passes that GET and
fails on the first PUT of the first real sync. These tests pin what the check
says in each case: write allowed, write refused, and write not verified.

The permission field is `icu_permission` of the athlete object, enum
`NONE | READ | WRITE` (intervals.icu OpenAPI `v1.0.0`, read 6 November 2026,
`GET /api/v1/athlete/{id}` -> schema `Athlete`). The document gives the enum
but no description, so an unknown or missing value is reported as
"not verified", never as a problem.
"""

from __future__ import annotations

import httpx
import pytest

from conftest import mock_refresh, store_token
from ghti.cli import main
from ghti.intervals import IntervalsClient

ATHLETE = "https://intervals.icu/api/v1/athlete/i42"
WELLNESS = "https://intervals.icu/api/v1/athlete/i42/wellness"


def mock_athlete(router, body=None, status: int = 200):
    """The athlete object `GET /athlete/{id}` answers with."""
    if body is None:
        body = {"id": "i42", "icu_permission": "WRITE"}
    return router.get(ATHLETE).mock(return_value=httpx.Response(status, json=body))


def ready(settings, router, athlete=None, status: int = 200):
    store_token(settings)
    mock_refresh(router)
    router.get(WELLNESS).mock(return_value=httpx.Response(200, json=[]))
    return mock_athlete(router, athlete, status)


# --- the client: one read-only request, the answer read as it is -------------


def test_write_permission_reads_the_field_of_the_athlete(settings, mock_http):
    route = mock_athlete(mock_http)
    client = IntervalsClient(httpx.Client(), "key", "i42")

    assert client.write_permission() == "WRITE"
    assert route.call_count == 1
    assert route.calls[0].request.method == "GET"  # nothing is written


def test_write_permission_is_unknown_when_the_field_is_absent(settings, mock_http):
    mock_athlete(mock_http, {"id": "i42"})
    assert IntervalsClient(httpx.Client(), "key", "i42").write_permission() is None


def test_write_permission_is_unknown_on_a_value_outside_the_enum(settings, mock_http):
    mock_athlete(mock_http, {"icu_permission": "SUPERUSER"})
    assert IntervalsClient(httpx.Client(), "key", "i42").write_permission() is None


def test_write_permission_is_unknown_when_the_answer_is_not_an_object(settings, mock_http):
    mock_athlete(mock_http, ["i42"])
    assert IntervalsClient(httpx.Client(), "key", "i42").write_permission() is None


def test_write_permission_is_unknown_when_the_answer_is_not_json(settings, mock_http):
    mock_http.get(ATHLETE).mock(return_value=httpx.Response(200, text="<html>nope"))
    assert IntervalsClient(httpx.Client(), "key", "i42").write_permission() is None


def test_write_permission_is_unknown_on_an_http_error(settings, mock_http):
    mock_athlete(mock_http, {}, status=500)
    assert IntervalsClient(httpx.Client(), "key", "i42").write_permission() is None


def test_write_permission_is_unknown_when_intervals_cannot_be_reached(settings, mock_http):
    mock_http.get(ATHLETE).mock(side_effect=httpx.ConnectError("down"))
    assert IntervalsClient(httpx.Client(), "key", "i42").write_permission() is None


def test_write_permission_counts_against_the_request_budget(settings, mock_http):
    mock_athlete(mock_http)
    client = IntervalsClient(httpx.Client(), "key", "i42")
    client.write_permission()
    assert client.requests == 1


# --- the check: what the person reads ----------------------------------------


def test_write_allowed_says_so_and_the_check_passes(settings, mock_http, capsys):
    route = ready(settings, mock_http)

    assert main(["check"]) == 0

    out = capsys.readouterr().out
    assert "The key reads and may write athlete i42." in out
    assert "Everything is ready" in out
    assert "PROBLEM" not in out
    assert route.call_count == 1


@pytest.mark.parametrize("permission", ["READ", "NONE"])
def test_a_read_only_key_is_a_configuration_problem(settings, mock_http, capsys, permission):
    ready(settings, mock_http, {"id": "i42", "icu_permission": permission})

    assert main(["check"]) == 2

    out = capsys.readouterr().out
    assert f"PROBLEM  intervals.icu: The key can read athlete i42 but not write to it (icu_permission {permission})" in out
    assert "INTERVALS_ATHLETE_ID" in out
    assert "Everything is ready" not in out
    assert "Google authorization" in out  # the check does not stop at this problem


def test_an_unknown_permission_is_a_note_and_promises_nothing(settings, mock_http, capsys):
    ready(settings, mock_http, {"id": "i42"})

    assert main(["check"]) == 0

    out = capsys.readouterr().out
    assert "note" in out
    assert "The key reads athlete i42. Writing is not verified" in out
    assert "will be written" not in out
    assert "A sync can run now." in out


def test_intervals_unreachable_for_the_permission_does_not_fail_the_check(settings, mock_http, capsys):
    store_token(settings)
    mock_refresh(mock_http)
    mock_http.get(WELLNESS).mock(return_value=httpx.Response(200, json=[]))
    mock_http.get(ATHLETE).mock(side_effect=httpx.ConnectError("down"))

    assert main(["check"]) == 0

    out = capsys.readouterr().out
    assert "Writing is not verified" in out
    assert "PROBLEM" not in out


def test_a_key_that_cannot_even_read_is_reported_before_the_permission(settings, mock_http, capsys):
    store_token(settings)
    mock_refresh(mock_http)
    mock_http.get(WELLNESS).mock(return_value=httpx.Response(401, json={}))
    route = mock_athlete(mock_http)

    assert main(["check"]) == 2

    out = capsys.readouterr().out
    assert "intervals.icu refused the request (HTTP 401)" in out
    assert "icu_permission" not in out
    assert route.call_count == 0  # no second request when the first one already failed


def test_the_permission_line_never_prints_the_key(settings, mock_http, capsys):
    from conftest import API_KEY

    ready(settings, mock_http, {"id": "i42", "icu_permission": "READ", "icu_api_key": API_KEY})

    assert main(["check"]) == 2
    assert API_KEY not in capsys.readouterr().out


def test_the_github_summary_carries_the_read_only_problem(settings, mock_http, github, capsys):
    ready(settings, mock_http, {"id": "i42", "icu_permission": "READ"})

    assert main(["check"]) == 2

    summary = github.read_text(encoding="utf-8")
    assert "- **PROBLEM** intervals.icu: The key can read athlete i42 but not write to it" in summary
