"""A gap must survive the runner, not just the machine that found it.

What was measured here on 6 November 2026, before this file existed. Same broken
run as tests/test_unfinished.py (a 200-day sync that writes 90 days and dies in
the second window), then `ghti status` with GITHUB_ACTIONS=true:

| where | exit code of `ghti status` | what the run page said |
|---|---|---|
| on a PC | 7, and the gap named | - |
| in the cloud | **0** | **"## Google authorization OK"** |

`cmd_status` read the gap out of the diary behind `if not in_actions()`, with the
reason written in the code: on GitHub the runner is new every time, so the diary
is always empty there. True, and it meant the cloud could not see a gap at all.

That matters because the cloud is where the buyer who installed this bridge with
"Use this template" lives, and because a red run is how this bridge reaches an
inbox: .github/workflows/reminder.yml turns red on purpose, every day, to send
one. That daily email went out while three months were missing and never
mentioned them - the only page that ever named the gap was the one of the run
that broke, months earlier, seen once.

So the gap is now kept as state next to the encrypted token, in the one
directory the workflows already commit (`state/`), and it outlives the runner.
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
from test_unfinished import RESUME_FROM, a_good_run, broke_halfway  # noqa: F401

RANGE_END = TODAY


@pytest.fixture
def cloud_status(settings, mock_http, github, capsys):
    """Run `ghti status` as a GitHub run does, with a clean run page each time."""

    def run(*extra: str):
        github.write_text("", encoding="utf-8")
        store_token(settings)
        mock_refresh(mock_http)
        _mock_everything(mock_http)
        from ghti.cli import main

        code = main(["status", *extra])
        out = capsys.readouterr().out
        return code, out, github.read_text(encoding="utf-8")

    return run


def test_the_cloud_sees_a_gap_the_runner_never_witnessed(broke_halfway, cloud_status):  # noqa: F811
    """The note is enough: no diary, no memory of the run that broke."""
    broke_halfway()
    journal.journal_file().unlink()  # a fresh runner: the diary does not exist

    code, out, page = cloud_status()

    assert code == PARTIAL, "a check standing over a gap must stay red, not go green"
    assert str(RESUME_FROM) in out and "still missing" in out
    assert "Stopped part-way" in page, "the page must not read like a run that wrote nothing"
    assert "Actions > Sync" in page and "**from**" in page, "the browser route comes first"
    assert f"ghti backfill --from {RESUME_FROM}" in page, "and the command for whoever has a PC"


def test_the_note_holds_dates_only(broke_halfway, settings):  # noqa: F811
    """It is committed to the buyer's own repository, which may be public."""
    broke_halfway()

    path = journal.gap_file()
    assert path == settings.token_file.parent / "gap.json", "next to the token, so state/ commits it"
    body = json.loads(path.read_text(encoding="utf-8"))
    assert set(body) == {"resume_from", "through", "seen_at"}
    assert body["resume_from"] == str(RESUME_FROM)
    assert body["through"] == str(RANGE_END)
    text = path.read_text(encoding="utf-8")
    for leaked in ("bad day", "HTTP 400", "steps", "weight", "90"):
        assert leaked not in text, f"no error text, no metric names, no counts: found {leaked!r}"


def test_a_run_that_fills_the_gap_clears_the_note(broke_halfway, a_good_run, cloud_status):  # noqa: F811
    """Green again only once the days are really on intervals.icu."""
    broke_halfway()
    assert journal.read_gap() is not None

    a_good_run(["backfill", "--from", str(RESUME_FROM), "--to", str(RANGE_END)])

    assert journal.read_gap() is None
    code, _, page = cloud_status()
    assert code == 0
    assert "Google authorization OK" in page


def test_a_run_that_fills_only_part_of_the_gap_keeps_the_note(broke_halfway, a_good_run, cloud_status):  # noqa: F811
    """Half of a gap closed is still a gap: three months stay named."""
    broke_halfway()

    a_good_run(["backfill", "--from", str(RESUME_FROM), "--to", str(RESUME_FROM + timedelta(days=9))])

    note = journal.read_gap()
    assert note is not None and note.resume_from == RESUME_FROM
    assert cloud_status()[0] == PARTIAL


def test_a_later_failure_does_not_make_the_gap_disappear(broke_halfway, settings, mock_http, capsys):  # noqa: F811
    """The nightly sync failing for another reason must not forget the missing months."""
    broke_halfway()
    store_token(settings)
    mock_refresh(mock_http)
    _mock_everything(mock_http)
    mock_http.put(INTERVALS_PUT).mock(return_value=httpx.Response(500, json={"error": "down"}))
    from ghti.cli import main

    assert main(["sync", "--days", "3"]) != 0
    capsys.readouterr()

    note = journal.read_gap()
    assert note is not None and note.resume_from == RESUME_FROM


def test_the_gap_wins_over_the_renewal_reminder_in_the_cloud(broke_halfway, cloud_status):  # noqa: F811
    """Measured 6 November 2026 on a PC; it has to hold in the cloud too.

    The missing days are already lost, the authorization is about tomorrow: the
    code belongs to the gap, and the reminder text is still printed.
    """
    broke_halfway()
    journal.journal_file().unlink()

    code, _, page = cloud_status("--remind-hours", "200", "--fail-on-reminder")

    assert code == PARTIAL, "not 5: a gap is not a renewal reminder"
    assert "Stopped part-way" in page


def test_a_note_nobody_can_read_is_no_note(broke_halfway, cloud_status):  # noqa: F811
    """A truncated or hand-edited file must not crash the daily check."""
    broke_halfway()
    journal.journal_file().unlink()
    journal.gap_file().write_text('{"resume_from": "not-a-day"', encoding="utf-8")

    assert journal.read_gap() is None
    assert cloud_status()[0] == 0


def test_a_backwards_note_is_ignored(broke_halfway):  # noqa: F811
    """`through` before `resume_from` describes no days at all."""
    broke_halfway()
    journal.write_gap(journal.Gap(resume_from=date(2026, 10, 1), through=date(2026, 9, 1)))

    assert journal.read_gap() is None
