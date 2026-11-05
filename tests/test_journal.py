"""The diary of the runs: what `ghti status` can say about yesterday's sync."""

from datetime import UTC, date, datetime, timedelta

import httpx

from conftest import GOOGLE_ROUTES, INTERVALS_PUT, NOW, mock_google, mock_refresh, store_token

GOOGLE_PATHS = [path for _, path in GOOGLE_ROUTES]
from ghti import journal
from ghti.cli import main


def ok_sync(mock_http):
    mock_refresh(mock_http)
    mock_google(mock_http)
    return mock_http.put(INTERVALS_PUT).mock(return_value=httpx.Response(200))


def test_sync_writes_one_line_and_status_reads_it(settings, mock_http, capsys):
    store_token(settings)
    ok_sync(mock_http)

    assert main(["sync"]) == 0
    capsys.readouterr()

    runs = journal.read()
    assert len(runs) == 1
    assert runs[0].command == "sync"
    assert runs[0].sent == 3
    assert runs[0].ok

    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "Last sync: 2026-10-22 09:00 UTC (today), 3 day(s) sent." in out
    assert "Google authorization" in out  # the token line is still there


def test_dry_run_writes_nothing(settings, mock_http, capsys):
    store_token(settings)
    ok_sync(mock_http)

    assert main(["sync", "--dry-run"]) == 0
    capsys.readouterr()

    assert journal.read() == []
    assert main(["status"]) == 0
    assert "No sync has run on this computer yet" in capsys.readouterr().out


def test_a_failed_sync_is_written_too(settings, mock_http, capsys):
    store_token(settings)
    mock_refresh(mock_http)
    # every Google metric fails: run_sync raises, and the failure must leave a trace
    bad = httpx.Response(400, json={"error": {"message": "bad request"}})
    mock_google(mock_http, overrides=dict.fromkeys(GOOGLE_PATHS, bad))

    assert main(["sync"]) == 1
    capsys.readouterr()

    run = journal.last()
    assert run is not None and not run.ok
    assert "Google Health" in run.error

    assert main(["status"]) == 0
    assert "FAILED: Nothing could be read from Google Health" in capsys.readouterr().out


def test_backfill_is_recorded_under_its_own_name(settings, mock_http, capsys):
    store_token(settings)
    ok_sync(mock_http)

    assert main(["backfill", "--from", "2026-10-20", "--to", "2026-10-22"]) == 0
    capsys.readouterr()

    assert journal.last().command == "backfill"


def test_status_warns_when_nothing_ran_for_days(settings, capsys):
    store_token(settings)
    four_days_ago = journal.Run(at=NOW - timedelta(days=4), sent=3, start=date(2026, 10, 15), end=date(2026, 10, 18))
    journal.append(four_days_ago)

    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "(4 days ago)" in out
    assert "Nothing has run for 4 days" in out
    assert "ghti schedule" in out


def test_one_day_old_run_is_not_called_stale(settings, capsys):
    store_token(settings)
    journal.append(journal.Run(at=NOW - timedelta(days=1), sent=1))

    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "(yesterday)" in out
    assert "Nothing has run" not in out


def test_failed_metrics_and_missing_permissions_are_named(settings):
    run = journal.Run(at=NOW, sent=2, failed=("hrv",), skipped=("weight",))
    text = journal.summary(run, now=NOW)
    assert "Some metrics failed: hrv." in text
    assert "Not authorized: weight." in text


def test_unreadable_lines_are_skipped(settings, tmp_path):
    path = journal.journal_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('not json\n{"at": "2026-10-22T09:00:00+00:00", "sent": 5}\n\n', encoding="utf-8")

    runs = journal.read()
    assert len(runs) == 1 and runs[0].sent == 5


def test_the_file_does_not_grow_forever(settings):
    for index in range(journal.KEEP + 10):
        journal.append(journal.Run(at=NOW, sent=index))
    assert len(journal.read()) == journal.KEEP
    assert journal.last().sent == journal.KEEP + 9


def test_the_diary_never_holds_values_or_secrets(settings, mock_http, capsys):
    store_token(settings)
    ok_sync(mock_http)
    assert main(["sync", "--show-values"]) == 0
    capsys.readouterr()
    text = journal.journal_file().read_text(encoding="utf-8")
    for secret in (settings.google_client_secret, settings.intervals_api_key):
        assert secret not in text
    for value in ("8123", "25200", "72.9"):  # steps, sleep, weight from the example data
        assert value not in text


def test_journal_file_can_be_moved(settings, tmp_path, monkeypatch):
    elsewhere = tmp_path / "diary" / "runs.jsonl"
    monkeypatch.setenv("GHTI_JOURNAL_FILE", str(elsewhere))
    journal.append(journal.Run(at=NOW, sent=1))
    assert elsewhere.is_file()


def test_a_broken_folder_does_not_stop_a_sync(settings, monkeypatch, tmp_path):
    blocked = tmp_path / "file-not-folder"
    blocked.write_text("x", encoding="utf-8")
    monkeypatch.setenv("GHTI_JOURNAL_FILE", str(blocked / "runs.jsonl"))
    journal.append(journal.Run(at=NOW, sent=1))  # must not raise
    assert journal.read() == []


def test_time_is_written_in_utc(settings):
    run = journal.Run(at=datetime(2026, 10, 22, 23, 30, tzinfo=UTC))
    assert run.as_dict()["at"] == "2026-10-22T23:30:00+00:00"
    assert journal.Run.from_dict(run.as_dict()) == run


# --- a run that dies on the setup ------------------------------------------------
# These three cover the silence that used to look like a scheduler problem: a daily
# task fires, the secrets are not where it can see them, the sync stops before the
# diary is written, and the next morning `ghti status` blamed the task.


def test_missing_settings_still_write_a_diary_line(monkeypatch, capsys):
    """No secrets at all: the failed run has to be readable the next morning."""
    assert main(["sync"]) == 2
    capsys.readouterr()

    runs = journal.read()
    assert len(runs) == 1
    assert runs[0].command == "sync"
    assert not runs[0].ok
    assert "Missing setting(s)" in runs[0].error
    assert "GOOGLE_CLIENT_ID" in runs[0].error

    # and status reports it as a failure, not as "nothing ever ran"
    out = journal.summary(journal.last(), setup_ok=False)
    assert "Last sync" in out
    assert "FAILED" in out
    assert "No sync has run" not in out


def test_dry_run_with_missing_settings_writes_nothing():
    """A dry run is a person at the keyboard, not a nightly run: no diary line."""
    assert main(["sync", "--dry-run"]) == 2
    assert journal.read() == []


def test_empty_diary_without_setup_does_not_blame_the_daily_task():
    out = journal.summary(None, setup_ok=False)
    assert "ghti check" in out
    assert "not firing" not in out
    assert "ghti sync" not in out  # pointless advice: it would fail the same way

    ok = journal.summary(None, setup_ok=True)
    assert "Run one with: ghti sync" in ok


def test_old_diary_without_setup_points_at_the_setup_first():
    old = journal.Run(
        at=NOW - timedelta(days=5),
        command="sync",
        start=date(2026, 10, 15),
        end=date(2026, 10, 17),
        sent=3,
    )
    out = journal.summary(old, setup_ok=False)
    assert "Nothing has run for 5 days." in out
    assert "ghti check" in out
    assert "not firing" not in out

    blamed = journal.summary(old, setup_ok=True)
    assert "the daily task is not firing" in blamed
    assert "ghti check" not in blamed
