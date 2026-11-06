import ast
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

from conftest import (
    CLIENT_SECRET,
    REFRESH_TOKEN,
    keys,
    mock_google,
    mock_refresh,
    store_token,
)
from ghti.cli import REMINDING, main
from ghti.oauth import REDIRECT_URI, TOKEN_ENDPOINT, TokenStore


def printed_link(text: str) -> str:
    return next(line for line in text.splitlines() if line.startswith("https://accounts.google.com/"))


def test_auth_url_then_auth_finish(settings, mock_http, capsys):
    assert main(["auth-url"]) == 0
    link = printed_link(capsys.readouterr().out)
    state = parse_qs(urlsplit(link).query)["state"][0]

    route = mock_http.post(TOKEN_ENDPOINT).mock(
        return_value=httpx.Response(200, json={"access_token": "ya29.x", "refresh_token": REFRESH_TOKEN})
    )
    address = f"{REDIRECT_URI}?state={state}&code=4/0AbcDef&scope=x"
    assert main(["auth-finish", address]) == 0

    out = capsys.readouterr().out
    assert "Google authorization saved. It expires on 2026-10-29 09:00 UTC." in out
    assert REFRESH_TOKEN not in out
    assert "code_verifier" in route.calls[0].request.content.decode()
    assert TokenStore(settings.token_file, keys()).load().refresh_token == REFRESH_TOKEN


def test_auth_finish_reads_address_from_env_on_github(settings, mock_http, github, monkeypatch, capsys):
    state = keys().make_state(0)
    monkeypatch.setenv("GHTI_REDIRECT_URL", f"{REDIRECT_URI}?state={state}&code=4/0AbcDef")
    mock_http.post(TOKEN_ENDPOINT).mock(
        return_value=httpx.Response(200, json={"access_token": "ya29.x", "refresh_token": REFRESH_TOKEN})
    )
    assert main(["auth-finish"]) == 0
    assert "::add-mask::4/0AbcDef" in capsys.readouterr().out
    assert "Google authorization saved" in github.read_text(encoding="utf-8")


def test_auth_finish_with_denied_access(settings, capsys):
    assert main(["auth-finish", f"{REDIRECT_URI}?error=access_denied"]) == 3
    assert "not granted" in capsys.readouterr().err


def test_status_reminds_on_day_six(settings, github, capsys):
    store_token(settings, age=timedelta(days=6))
    assert main(["status", "--fail-on-reminder"]) == REMINDING
    summary = github.read_text(encoding="utf-8")
    assert "Reminder: the Google authorization expires tomorrow" in summary
    assert "[Authorize Google Health](https://accounts.google.com/" in summary
    assert main(["status"]) == 0  # without the flag the reminder does not fail the job


def test_status_ok_early_in_the_week(settings, github, capsys):
    store_token(settings, age=timedelta(days=2))
    assert main(["status", "--fail-on-reminder"]) == 0
    assert "expires on 2026-10-27" in capsys.readouterr().out
    assert "Reminder" not in github.read_text(encoding="utf-8")


def test_status_when_expired(settings, capsys):
    store_token(settings, age=timedelta(days=7, hours=1))
    assert main(["status"]) == 3
    assert "expired" in capsys.readouterr().out


def test_export_csv(settings, mock_http, tmp_path, capsys):
    store_token(settings)
    mock_refresh(mock_http)
    mock_google(mock_http)
    out_file = tmp_path / "wellness.csv"
    assert main(["export-csv", "--days", "3", "-o", str(out_file)]) == 0
    lines = out_file.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "date,sleepSecs,restingHR,hrv,weight,bodyFat,spO2,steps,floorsClimbed,vo2max,respiration"
    assert lines[2] == "2026-10-21,25200,52,48.3,72.9,18.9,96.4,10456,7,,"
    assert len(lines) == 4


def test_missing_settings_are_listed(capsys):
    assert main(["sync"]) == 2
    err = capsys.readouterr().err
    assert "GOOGLE_CLIENT_ID" in err and "INTERVALS_API_KEY" in err


def test_unconfigured_template_repository_is_skipped(github, capsys):
    assert main(["sync", "--skip-if-unconfigured"]) == 0
    assert "Not configured yet" in capsys.readouterr().out


def test_config_file_is_used_on_a_computer(tmp_path, monkeypatch, capsys):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.env").write_text(
        f'GOOGLE_CLIENT_ID=abc.apps.googleusercontent.com\nGOOGLE_CLIENT_SECRET="{CLIENT_SECRET}"\nINTERVALS_API_KEY=k\n',
        encoding="utf-8",
    )
    assert main(["auth-url"]) == 0
    assert "client_id=abc.apps.googleusercontent.com" in capsys.readouterr().out


def test_invalid_timezone(settings, monkeypatch, capsys):
    monkeypatch.setenv("GHTI_TIMEZONE", "Mars/Olympus")
    assert main(["sync"]) == 2
    assert "Europe/Rome" in capsys.readouterr().err


def test_status_in_unconfigured_template_is_skipped(github):
    assert main(["status", "--skip-if-unconfigured", "--fail-on-reminder"]) == 0


def test_error_summary_says_where_to_look(github):
    assert main(["sync"]) == 2
    summary = github.read_text(encoding="utf-8")
    assert "Missing setting(s)" in summary
    assert "https://github.com/antivex/google-health-to-intervals#error-messages-one-by-one" in summary
    assert "https://github.com/antivex/google-health-to-intervals/issues/new" in summary
    assert "https://antivex.gumroad.com/l/fitbit-google-health-intervals" in summary


def test_error_on_a_computer_points_to_the_error_list(capsys):
    assert main(["sync"]) == 2
    err = capsys.readouterr().err
    assert "#error-messages-one-by-one" in err and "issues/new" in err
    assert "gumroad" not in err  # one line only in a terminal, no sales pitch


ERROR_CLASSES = {
    "GhtiError",
    "ConfigError",
    "AuthRequired",
    "AuthError",
    "GoogleHealthError",
    "IntervalsError",
    "DecryptError",
    "ArgumentTypeError",
}
MIN_KEY = 12  # shorter than this is not enough to look a message up
KEY_LENGTH = 24  # how much of a fixed piece we expect to find, word for word


def fixed_pieces(node: ast.AST) -> list[str]:
    """The parts of a message that do not change with the data (no placeholders)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.JoinedStr):
        return [v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str)]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return fixed_pieces(node.left) + fixed_pieces(node.right)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return fixed_pieces(node.func.value)  # f"...".strip()
    return []


def error_messages() -> list[tuple[str, int, list[str]]]:
    """Every message the bridge builds for a person, read from the source itself."""
    found = []
    for path in sorted((Path(__file__).resolve().parent.parent / "src" / "ghti").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            if name not in ERROR_CLASSES or not node.args:
                continue
            keys = [piece.strip()[:KEY_LENGTH] for piece in fixed_pieces(node.args[0])]
            keys = [key for key in keys if len(key) >= MIN_KEY]
            if keys:
                found.append((path.name, node.lineno, keys))
    return found


def test_readme_error_list_covers_the_real_messages():
    """The footer promises a list with every message: keep that promise honest.

    The list of messages comes from the code, not from this test, so a new message
    without its line in the README makes the tests fail.
    """
    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")
    section = readme.split("## Error messages, one by one", 1)[1].split("\n## ", 1)[0]
    messages = error_messages()
    assert len(messages) >= 20, "the messages are no longer being found in the source"
    missing = [
        f"{name}:{line} -> {keys[0]!r}" for name, line, keys in messages if not any(key in section for key in keys)
    ]
    assert not missing, "messages with no line in the README error list: " + "; ".join(missing)


# --- an expiry date Google never gave ------------------------------------------
# The 7 days come from the "Testing" publishing status, and Google says so in
# refresh_token_expires_in. Without that field our date is a guess: a guess must
# never stop a working sync, and must never ask the owner to click for nothing.


def test_status_asks_google_instead_of_stopping_on_a_guessed_expiry(settings, mock_http, capsys):
    from conftest import mock_refresh
    from ghti.oauth import TokenStore
    from ghti.crypto import Keys
    from conftest import CLIENT_SECRET, API_KEY

    store_token(settings, age=timedelta(days=9), expiry_known=False)
    mock_refresh(mock_http)

    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "the authorization still works" in out
    # the guess moved on, and stays a guess
    token = TokenStore(settings.token_file, Keys(CLIENT_SECRET, API_KEY)).load()
    assert token.remaining() > timedelta(days=6)
    assert token.expiry_known is False


def test_status_still_asks_to_authorize_when_google_refuses_the_guessed_token(settings, mock_http, capsys):
    from conftest import mock_refresh

    store_token(settings, age=timedelta(days=9), expiry_known=False)
    mock_refresh(mock_http, status=400, body={"error": "invalid_grant", "error_description": "Token expired"})

    assert main(["status"]) == 3  # 3 = authorization needed, as before
    assert "expired or was revoked" in capsys.readouterr().out


def test_a_guessed_date_is_not_told_as_a_fact(settings, github, capsys):
    store_token(settings, age=timedelta(days=2), expiry_known=False)
    assert main(["status", "--fail-on-reminder"]) == 0
    assert "should expire on 2026-10-27" in capsys.readouterr().out


def test_the_day_six_reminder_says_when_the_date_is_only_a_guess(settings, github, capsys):
    store_token(settings, age=timedelta(days=6), expiry_known=False)
    assert main(["status", "--fail-on-reminder"]) == REMINDING
    summary = github.read_text(encoding="utf-8")
    assert "may expire tomorrow" in summary
    assert "Google did not give a date for this one" in summary


# --- the reminder has a code of its own ------------------------------------------
# Measured on 6 November 2026: with --fail-on-reminder, the day-6 reminder and a
# status that could not even reach Google both exited 1. These four tests keep
# the two apart.


def test_the_reminder_code_is_none_of_the_failure_codes():
    """If someone gives 5 to an error tomorrow, the reminder stops being readable."""
    from ghti import errors
    from ghti.gaps import FOUND

    failures = {
        cls.exit_code
        for cls in vars(errors).values()
        if isinstance(cls, type) and issubclass(cls, errors.GhtiError)
    }
    assert REMINDING not in failures
    assert REMINDING not in (0, FOUND)  # 0 = nothing to do, 4 = days are missing


def test_a_status_that_could_not_ask_does_not_look_like_the_reminder(settings, github, mock_http, capsys):
    """The silent case: a check that never ran must not read as "renew it tomorrow"."""
    store_token(settings, age=timedelta(days=8), expiry_known=False)
    mock_http.post(TOKEN_ENDPOINT).mock(side_effect=httpx.ConnectError("network down"))

    code = main(["status", "--fail-on-reminder"])
    assert code == 1  # a failure, as before
    assert code != REMINDING


def test_an_expired_authorization_is_not_the_reminder(settings, github, capsys):
    """"It expired" and "it expires tomorrow" are two different jobs for you."""
    store_token(settings, age=timedelta(days=7, hours=1))
    code = main(["status", "--fail-on-reminder"])
    assert code == 3  # authorization needed, as before
    assert code != REMINDING


def test_the_reminder_code_is_written_where_a_scheduled_task_reads_it():
    """Whoever puts `status` in a scheduled task reads its --help, not this file."""
    from ghti.cli import build_parser

    status = build_parser()._subparsers._group_actions[0].choices["status"]
    text = status.format_help()
    assert str(REMINDING) in text, "`ghti status --help` never says the reminder code"
    assert "not 1 on purpose" in text
    assert str(REMINDING) in [line for line in text.splitlines() if "--fail-on-reminder" in line][-1]
