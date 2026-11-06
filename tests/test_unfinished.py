"""A gap left by a broken backfill must survive the next successful run.

A long backfill that dies halfway already prints where it stopped and the
command that carries on (tests/test_resume.py). What was measured here on
6 November 2026, before this file existed:

1. the diary line of the break said `sent: 0` while 90 days were really on
   intervals.icu, and wrote the resume day only inside an English sentence,
   so no program could read it;
2. `ghti status` reads the last line of the diary. The next morning the daily
   sync succeeded, became the last line, and from that moment nothing named
   the gap again: `Last sync: ..., 3 day(s) sent.` and 110 missing days.

Nobody watches a scheduled run. The buyer of the guide looks at `ghti status`,
sees a green line, and never learns that three months of history are missing.
So the break is now written down as fields (days really sent, first day still
missing) and `ghti status` keeps naming the gap until a run actually fills it.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import httpx
import pytest

from conftest import INTERVALS_PUT, mock_refresh, store_token
from ghti import journal
from ghti.sync import PARTIAL
from test_long_range import LONG_DAYS, TODAY, _mock_everything

FIRST_WINDOW_END = date(2026, 7, 4)  # 90 days from 2026-04-06
RESUME_FROM = FIRST_WINDOW_END + timedelta(days=1)  # 2026-07-05
RANGE_START = TODAY - timedelta(days=LONG_DAYS - 1)  # 2026-04-06


@pytest.fixture
def broke_halfway(settings, mock_http, capsys):
    """A 200-day sync whose second window is refused: 90 days land, 110 do not."""

    def arrange():
        store_token(settings)
        mock_refresh(mock_http)
        _mock_everything(mock_http)
        mock_http.put(INTERVALS_PUT).mock(
            side_effect=[httpx.Response(200), httpx.Response(400, json={"error": "bad day"})]
        )
        from ghti.cli import main

        assert main(["sync", "--days", str(LONG_DAYS)]) == PARTIAL
        capsys.readouterr()

    return arrange


@pytest.fixture
def a_good_run(settings, mock_http, capsys):
    """A later run that succeeds, with the range it covers."""

    def arrange(argv: list[str]):
        mock_http.reset()
        mock_refresh(mock_http)
        _mock_everything(mock_http)
        mock_http.put(INTERVALS_PUT).mock(return_value=httpx.Response(200))
        from ghti.cli import main

        assert main(argv) == 0
        capsys.readouterr()

    return arrange


def status(capsys) -> str:
    from ghti.cli import main

    assert main(["status"]) == 0
    return capsys.readouterr().out


def diary_lines() -> list[dict]:
    text = journal.journal_file().read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


# --- what the diary writes down about the break --------------------------------


def test_the_diary_counts_the_days_that_really_landed(broke_halfway):
    """`sent: 0` was a lie: 90 days are on intervals.icu and cannot be unsent."""
    broke_halfway()

    assert diary_lines()[-1]["sent"] == 90


def test_the_diary_names_the_first_day_still_missing(broke_halfway):
    broke_halfway()

    assert diary_lines()[-1]["resume_from"] == RESUME_FROM.isoformat()


def test_the_diary_keeps_the_end_that_was_asked_for(broke_halfway):
    """Without the end of the range the gap has no second edge."""
    broke_halfway()

    assert diary_lines()[-1]["end"] == TODAY.isoformat()


def test_a_failure_that_wrote_nothing_has_no_resume_day(settings, mock_http, capsys):
    """The first window fails: there is no safe `--from`, so none is recorded."""
    store_token(settings)
    mock_refresh(mock_http)
    _mock_everything(mock_http)
    mock_http.put(INTERVALS_PUT).mock(return_value=httpx.Response(400, json={"error": "bad day"}))
    from ghti.cli import main

    assert main(["sync", "--days", str(LONG_DAYS)]) == 1  # nothing landed: not PARTIAL
    capsys.readouterr()

    line = diary_lines()[-1]
    assert line["resume_from"] is None
    assert line["sent"] == 0
    assert journal.unfinished() is None


def test_the_diary_still_holds_no_values(broke_halfway):
    """A date and a count are not measurements: the diary stays safe to attach."""
    broke_halfway()

    text = journal.journal_file().read_text(encoding="utf-8")
    for forbidden in ("restingHR", "sleepSecs", "test-client-secret", "test-intervals-key"):
        assert forbidden not in text


# --- what `ghti status` says the morning after ---------------------------------


def test_the_morning_after_a_good_sync_still_names_the_gap(broke_halfway, a_good_run, capsys):
    """The defect this file exists for: the green line used to hide the gap."""
    broke_halfway()
    a_good_run(["sync", "--days", "3", "--no-confirm"])

    out = status(capsys)

    assert "3 day(s) sent" in out, out  # the good run is still reported
    assert f"{RESUME_FROM} to {TODAY} are still missing" in out, out


def test_the_morning_after_names_the_command_that_ends_it(broke_halfway, a_good_run, capsys):
    broke_halfway()
    a_good_run(["sync", "--days", "3", "--no-confirm"])

    assert f"ghti backfill --from {RESUME_FROM} --to {TODAY}" in status(capsys)


def test_the_break_itself_does_not_say_it_twice(broke_halfway, capsys):
    """Right after the break the error text already names the gap."""
    broke_halfway()

    out = status(capsys)

    assert "FAILED" in out, out
    assert "still missing" not in out, out
    assert out.count(f"--from {RESUME_FROM}") == 1, out


def test_a_backfill_that_fills_the_gap_clears_it(broke_halfway, a_good_run, capsys):
    broke_halfway()
    a_good_run(["backfill", "--from", RESUME_FROM.isoformat(), "--to", TODAY.isoformat(), "--no-confirm"])

    out = status(capsys)

    assert "still missing" not in out, out
    assert journal.unfinished() is None


def test_a_wider_backfill_clears_it_too(broke_halfway, a_good_run, capsys):
    """Running the whole range again is wasteful but it does fill the gap."""
    broke_halfway()
    a_good_run(["backfill", "--from", RANGE_START.isoformat(), "--to", TODAY.isoformat(), "--no-confirm"])

    assert "still missing" not in status(capsys)


def test_a_backfill_that_stops_short_of_the_end_does_not_clear_it(broke_halfway, a_good_run, capsys):
    """Covering 2026-07-05..2026-09-30 leaves October missing: still a gap."""
    broke_halfway()
    short_end = date(2026, 9, 30)
    a_good_run(["backfill", "--from", RESUME_FROM.isoformat(), "--to", short_end.isoformat(), "--no-confirm"])

    assert "still missing" in status(capsys)


def test_the_daily_three_day_sync_never_clears_a_long_gap(broke_halfway, a_good_run, capsys):
    """Thirty green mornings in a row do not write a single missing day."""
    broke_halfway()
    for _ in range(3):
        a_good_run(["sync", "--days", "3", "--no-confirm"])

    assert "still missing" in status(capsys)


def test_a_later_failure_does_not_count_as_filling_the_gap(broke_halfway, settings, mock_http, capsys):
    broke_halfway()
    mock_http.reset()
    mock_refresh(mock_http)
    _mock_everything(mock_http)
    mock_http.put(INTERVALS_PUT).mock(return_value=httpx.Response(400, json={"error": "bad day"}))
    from ghti.cli import main

    # this second run writes nothing of its own, so it is 1, not PARTIAL
    assert main(["backfill", "--from", RESUME_FROM.isoformat(), "--to", TODAY.isoformat()]) == 1
    capsys.readouterr()

    assert journal.unfinished() is not None


def test_the_newest_break_is_the_one_reported(broke_halfway, settings, mock_http, capsys):
    """Two breaks: the resume command of the newer one covers the older range."""
    broke_halfway()
    mock_http.reset()
    mock_refresh(mock_http)
    _mock_everything(mock_http)
    mock_http.put(INTERVALS_PUT).mock(
        side_effect=[httpx.Response(200), httpx.Response(400, json={"error": "bad day"})]
    )
    from ghti.cli import main

    assert main(["backfill", "--from", RESUME_FROM.isoformat(), "--to", TODAY.isoformat()]) == PARTIAL
    capsys.readouterr()

    run = journal.unfinished()
    assert run is not None
    assert run.resume_from == RESUME_FROM + timedelta(days=90)


# --- the diary file itself ------------------------------------------------------


def test_old_diary_lines_without_the_field_are_still_readable(settings, tmp_path):
    """The field is new: a diary written last week must not break `ghti status`."""
    path = tmp_path / "old.jsonl"
    path.write_text(
        json.dumps(
            {"at": "2026-11-01T06:00:00+00:00", "command": "sync", "start": "2026-11-01", "end": "2026-11-01", "sent": 1}
        )
        + "\n",
        encoding="utf-8",
    )

    runs = journal.read(path)

    assert len(runs) == 1
    assert runs[0].resume_from is None
    assert journal.unfinished(path) is None


def test_a_resume_day_after_the_end_is_not_a_gap():
    """Defensive: a range whose last window broke has nothing left to fill."""
    run = journal.Run(
        at=journal.datetime(2026, 11, 1, tzinfo=journal.UTC),
        start=date(2026, 11, 1),
        end=date(2026, 11, 2),
        resume_from=date(2026, 11, 3),
        error="boom",
    )

    assert run.gap is None
    assert journal.unfinished(runs=[run]) is None


def test_a_dry_run_that_breaks_halfway_writes_no_gap(settings, mock_http, capsys):
    """`--dry-run` writes nothing to intervals.icu, so there is no gap to fill."""
    store_token(settings)
    mock_refresh(mock_http)
    _mock_everything(mock_http)
    mock_http.put(INTERVALS_PUT).mock(
        side_effect=[httpx.Response(200), httpx.Response(400, json={"error": "bad day"})]
    )
    from ghti.cli import main

    main(["sync", "--days", str(LONG_DAYS), "--dry-run"])
    capsys.readouterr()

    assert journal.unfinished() is None


def test_an_expired_authorization_halfway_also_carries_the_gap():
    """`AuthRequired` keeps its own class so the CLI still shows the link.

    That branch builds a fresh exception instead of `SyncInterrupted`, so it
    would lose the fields unless they are attached by hand. Today nothing
    raises it between two windows (the token is refreshed once, before the
    first one), so this is checked on the function itself: a future change
    that moves the refresh inside the loop must not quietly stop writing the
    gap into the diary.
    """
    from ghti.errors import AuthRequired
    from ghti.sync import SyncReport, _interrupted

    report = SyncReport(RANGE_START, TODAY)
    report.records_sent = 90

    error = _interrupted(AuthRequired("the authorization expired"), FIRST_WINDOW_END, TODAY, report)

    assert isinstance(error, AuthRequired)  # the CLI still prints the Google link
    assert error.resume_from == RESUME_FROM
    assert error.written == 90
    assert error.done_through == FIRST_WINDOW_END
    assert error.asked_end == TODAY
