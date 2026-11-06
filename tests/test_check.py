"""`ghti check`: the setup check a person runs before (or instead of) the first sync."""

from datetime import timedelta

import httpx

from conftest import API_KEY, mock_refresh, store_token
from ghti.check import MISSING, PROBLEM, WAITING, Step, exit_code
from ghti.cli import main

WELLNESS = "https://intervals.icu/api/v1/athlete/i42/wellness"


def mock_intervals(router, status: int = 200):
    return router.get(WELLNESS).mock(return_value=httpx.Response(status, json=[]))


def test_everything_ready(settings, mock_http, capsys):
    store_token(settings)
    mock_refresh(mock_http)
    route = mock_intervals(mock_http)

    assert main(["check"]) == 0

    out = capsys.readouterr().out
    assert "Everything is ready" in out
    assert "Days are cut at midnight in Europe/Rome." in out
    assert "The key reads and may write athlete i42." in out
    assert "It expires on 2026-10-28 09:00 UTC (in 144 h)." in out
    assert "MISSING" not in out and "PROBLEM" not in out
    assert API_KEY not in out
    assert route.call_count == 1  # one read-only request, and nothing is written


def test_missing_secrets_names_them_and_touches_no_network(capsys):
    # WAITING, not 2: nothing is broken here, the secrets were simply never written.
    assert main(["check"]) == WAITING
    out = capsys.readouterr().out
    assert "MISSING" in out
    for name in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "INTERVALS_API_KEY"):
        assert name in out
    assert "Settings > Secrets and variables > Actions" in out
    assert "Fix the lines marked MISSING or PROBLEM" in out


def test_wrong_intervals_key_is_a_configuration_problem(settings, mock_http, capsys):
    store_token(settings)
    mock_refresh(mock_http)
    mock_intervals(mock_http, status=401)

    assert main(["check"]) == 2

    out = capsys.readouterr().out
    assert "PROBLEM  intervals.icu: intervals.icu refused the request (HTTP 401)" in out
    assert "Google authorization" in out  # the check does not stop at the first problem


def test_no_authorization_yet_says_what_to_run(settings, mock_http, capsys):
    mock_intervals(mock_http)
    assert main(["check"]) == WAITING
    out = capsys.readouterr().out
    assert "MISSING" in out
    assert "No Google authorization found" in out
    assert "ghti auth-url" in out


def test_expiring_authorization_asks_for_a_renewal(settings, mock_http, capsys):
    store_token(settings, age=timedelta(days=6))
    mock_refresh(mock_http)
    mock_intervals(mock_http)

    assert main(["check"]) == 0

    out = capsys.readouterr().out
    assert "Fewer than 36 hours left (24 h)" in out
    assert "A sync can run now." in out


def test_missing_timezone_is_only_a_note(settings, mock_http, monkeypatch, capsys):
    monkeypatch.delenv("GHTI_TIMEZONE")
    store_token(settings)
    mock_refresh(mock_http)
    mock_intervals(mock_http)

    assert main(["check"]) == 0

    out = capsys.readouterr().out
    assert "GHTI_TIMEZONE is not set" in out
    assert "A sync can run now." in out


def test_partial_permissions_are_reported(settings, mock_http, capsys):
    from conftest import NOW, keys
    from ghti.oauth import SCOPES, StoredToken, TokenStore

    token = StoredToken("1//r", NOW, NOW + timedelta(days=7), [SCOPES[0]], expiry_known=True)
    TokenStore(settings.token_file, keys()).save(token)
    mock_refresh(mock_http)
    mock_intervals(mock_http)

    assert main(["check"]) == 0
    out = capsys.readouterr().out
    assert "Not granted: health_metrics_and_measurements, activity_and_fitness" in out


def test_on_github_the_summary_holds_the_checklist_and_where_to_ask(settings, mock_http, github, capsys):
    mock_intervals(mock_http, status=403)
    store_token(settings)
    mock_refresh(mock_http)

    assert main(["check"]) == 2

    summary = github.read_text(encoding="utf-8")
    assert "## Setup check" in summary
    assert "- **PROBLEM** intervals.icu:" in summary
    assert "Stuck on this error?" in summary
    assert "open an issue" in summary


# --- the exit code: "your turn" is not "it broke" ------------------------------
#
# Measured on 6 November 2026: before any setting existed the check exited 2,
# and so did a check whose intervals.icu key was refused with HTTP 401. Same
# code for a setup still to finish and a setup that stopped working.


def test_waiting_is_not_one_of_the_codes_an_error_uses():
    from ghti import gaps
    from ghti.cli import REMINDING

    assert WAITING not in (0, 1, 2, 3)
    assert WAITING != gaps.FOUND  # 4: days are missing
    assert WAITING != REMINDING  # 5: renew the authorization soon


def test_a_revoked_authorization_is_still_your_turn(settings, mock_http, capsys):
    store_token(settings)
    mock_refresh(mock_http, status=400, body={"error": "invalid_grant"})
    mock_intervals(mock_http)

    assert main(["check"]) == WAITING

    out = capsys.readouterr().out
    assert "MISSING  Google authorization" in out
    assert "ghti auth-url" in out


def test_a_refused_key_still_exits_two_and_not_waiting(settings, mock_http, capsys):
    store_token(settings)
    mock_refresh(mock_http)
    mock_intervals(mock_http, status=401)

    # A key that is refused is a problem, not a step waiting for the person.
    assert main(["check"]) == 2


def test_one_problem_wins_over_the_lines_that_only_wait(settings, mock_http, capsys):
    # Dead network on intervals.icu (PROBLEM, 1) and no Google token yet (MISSING).
    # The broken line decides: a scheduled task must not read this as "your turn".
    mock_http.get(WELLNESS).mock(side_effect=httpx.ConnectError("down"))

    assert main(["check"]) == 1

    out = capsys.readouterr().out
    assert "PROBLEM" in out and "MISSING" in out


def test_a_bad_line_without_a_code_never_exits_zero():
    # No check builds one today; if one ever forgets its code, a bad line must
    # not go out as "everything is ready".
    assert exit_code([Step(PROBLEM, "Invented", "no code given")]) == 1
    assert exit_code([Step(MISSING, "Invented", "no code given")]) == WAITING
