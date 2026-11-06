"""The note of an open gap has to reach the repository, not just the runner.

`state/` is the whole memory this bridge has in the cloud: the runner is new
every time, so a gap written down by last night's run is known to tonight's run
only because a `git push` carried it there. That push can be refused.

Measured on 6 November 2026 against the step these tests now replace
(`git add -A state` ... `git pull --rebase --quiet` ... `git push`), in a
sandbox repository with a bare remote:

| what happened to the push | the step | the note |
|---|---|---|
| a commit landed on the branch in between | exit 1, rebase stopped in CONFLICT | never reached the remote |
| the branch asks for a pull request | exit 1, `[remote rejected]` | never reached the remote |

And the cost of losing it is the bug of that same morning, back again:
`test_losing_the_note_puts_the_silence_back` below runs `ghti status` in the
cloud over a gap whose note never arrived and watches the run page read
"Google authorization OK".

Nothing rediscovers such a gap on its own, by design: `ghti gaps` asks
intervals.icu, but no workflow may run it on a schedule, because a day nobody
wore the watch looks exactly like a day that was lost (see ghti/gaps.py). The
note is the memory. So the script retries, and when it truly cannot save it
says on the run page which days are missing and how to close them.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import mock_refresh, store_token
from ghti import journal
from test_long_range import _mock_everything
from test_unfinished import RESUME_FROM, broke_halfway  # noqa: F401

SCRIPT = Path(__file__).resolve().parents[1] / ".github" / "save-state.sh"
BASH = shutil.which("bash")
needs_bash = pytest.mark.skipif(BASH is None, reason="the save step is bash; no bash here")

NOTE = '{"resume_from": "2026-07-05", "through": "2026-10-22", "seen_at": "2026-11-06T12:30:00+00:00"}\n'


def git(*args: str, cwd: Path, check: bool = True) -> str:
    done = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=check
    )
    return done.stdout.strip()


@pytest.fixture
def sandbox(tmp_path):
    """A bare remote, the runner's clone, and a second clone for whoever else pushes."""
    remote = tmp_path / "remote.git"
    git("init", "--quiet", "--bare", "--initial-branch=main", str(remote), cwd=tmp_path)
    runner = tmp_path / "runner"
    git("clone", "--quiet", str(remote), str(runner), cwd=tmp_path)
    git("config", "user.email", "runner@example.com", cwd=runner)
    git("config", "user.name", "runner", cwd=runner)
    (runner / "state").mkdir()
    (runner / "state" / "README.md").write_text("state\n", encoding="utf-8")
    git("add", "-A", cwd=runner)
    git("commit", "--quiet", "-m", "init", cwd=runner)
    git("push", "--quiet", "origin", "HEAD:refs/heads/main", cwd=runner)
    git("branch", "--set-upstream-to=origin/main", "main", cwd=runner, check=False)
    other = tmp_path / "other"
    git("clone", "--quiet", str(remote), str(other), cwd=tmp_path)
    git("config", "user.email", "other@example.com", cwd=other)
    git("config", "user.name", "other", cwd=other)
    return remote, runner, other


def save(runner: Path, page: Path | None = None, attempts: str = "3"):
    env = {
        "PATH": __import__("os").environ["PATH"],
        "GITHUB_REF_NAME": "main",
        "GHTI_SAVE_ATTEMPTS": attempts,
        "HOME": str(runner.parent),
    }
    if page is not None:
        env["GITHUB_STEP_SUMMARY"] = str(page)
    done = subprocess.run(
        [BASH, str(SCRIPT)], cwd=runner, capture_output=True, text=True, env=env
    )
    return done.returncode, done.stdout + done.stderr


def on_the_remote(remote: Path, path: str = "state/gap.json") -> str:
    return subprocess.run(
        ["git", "show", f"main:{path}"],
        cwd=remote,
        capture_output=True,
        text=True,
    ).stdout


@needs_bash
def test_the_note_reaches_the_remote(sandbox):
    remote, runner, _ = sandbox
    (runner / "state" / "gap.json").write_text(NOTE, encoding="utf-8")

    code, out = save(runner)

    assert code == 0, out
    assert "2026-07-05" in on_the_remote(remote), "the note never left the runner"


@needs_bash
def test_a_push_that_lost_a_race_is_retried_not_abandoned(sandbox):
    """The old step rebased onto the branch and stopped in CONFLICT, exit 1.

    `concurrency: ghti-state` serialises our own workflows, but the buyer's own
    commit is outside that lock, and so is the window between pull and push.
    """
    remote, runner, other = sandbox
    (other / "state" / "gap.json").write_text('{"resume_from": "2026-02-01", "through": "2026-02-03"}\n', encoding="utf-8")
    git("add", "-A", cwd=other)
    git("commit", "--quiet", "-m", "someone else touches state/", cwd=other)
    git("push", "--quiet", cwd=other)

    (runner / "state" / "gap.json").write_text(NOTE, encoding="utf-8")
    code, out = save(runner)

    assert code == 0, out
    assert "2026-07-05" in on_the_remote(remote), "the run's own note must win: it is the fresh one"
    assert "someone else touches state/" in git("log", "--oneline", "main", cwd=remote)


@needs_bash
def test_a_branch_that_refuses_the_push_gets_the_days_on_the_run_page(sandbox, tmp_path):
    """A protected branch, or read-only Actions permissions: nothing to work around.

    What must not happen is the run page showing a git error and nothing else,
    because those dates are then gone for good.
    """
    remote, runner, _ = sandbox
    hook = remote / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho 'Changes must be made through a pull request.' >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    (runner / "state" / "gap.json").write_text(NOTE, encoding="utf-8")
    page = tmp_path / "summary.md"
    page.write_text("", encoding="utf-8")

    code, out = save(runner, page=page)

    assert code != 0, "a run that lost what it found must stay red"
    text = page.read_text(encoding="utf-8")
    assert "2026-07-05" in text and "2026-10-22" in text, "the page must name the days"
    assert "Actions > Sync > Run workflow" in text, "the browser route comes first"
    assert "ghti backfill --from 2026-07-05 --to 2026-10-22" in text
    assert "Workflow permissions" in text, "and why a push gets refused"
    assert "Push 3 of 3" in out


@needs_bash
def test_nothing_new_in_state_is_not_a_commit(sandbox):
    remote, runner, _ = sandbox
    code, out = save(runner)
    assert code == 0 and "Nothing new" in out
    assert git("log", "--oneline", "main", cwd=remote).count("\n") == 0


def test_losing_the_note_puts_the_silence_back(broke_halfway, settings, mock_http, github, capsys):  # noqa: F811
    """Why the push matters: with no note, the cloud goes green over the gap.

    This is the bug of 6 November 2026 (tests/test_gap_in_cloud.py) reached by a
    different road: not a gap the code forgot to look for, but a gap whose note
    never got out of the runner.
    """
    broke_halfway()
    note = journal.gap_file()
    assert note.exists(), "the run that broke writes it"
    note.unlink()  # the push was refused: the next runner checks out a state/ without it
    journal.journal_file().unlink()  # and the diary is always empty on a fresh runner

    github.write_text("", encoding="utf-8")
    store_token(settings)
    mock_refresh(mock_http)
    _mock_everything(mock_http)
    from ghti.cli import main

    code = main(["status"])
    capsys.readouterr()

    assert code == 0, "measured: it exits 0 over the missing months"
    assert "Google authorization OK" in github.read_text(encoding="utf-8")
