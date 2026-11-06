"""The GitHub Actions side of the bridge, checked as text.

Three of the commands can hand back a NEW refresh token (Google is allowed to
rotate it on every refresh: see `refresh_access_token`, which returns
`data.get("refresh_token")`). When that happens the old refresh token stops
working, so the run that received the new one MUST be able to commit it back to
`state/`. A workflow that runs such a command without `contents: write` and
without the commit step loses it, and every later sync then fails with an
expired authorization — the worst kind of failure, because nobody touched
anything.

Read as text on purpose: PyYAML is not a dependency of this project and these
files are small and flat.
"""

from __future__ import annotations

from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"

# Commands whose code path reaches TokenStore.save():
#   auth-finish -> cli.cmd_auth_finish
#   check       -> check.run (open_google refreshes and saves)
#   status      -> cli._ask_google_if_alive, once the 7-day guess has run out
#   sync        -> sync.run
#   backfill    -> sync.run
SAVES_THE_TOKEN = ("auth-finish", "check", "status", "sync", "backfill")


def workflows() -> dict[str, str]:
    files = sorted(WORKFLOWS.glob("*.yml"))
    assert files, f"no workflow found in {WORKFLOWS}"
    return {path.name: path.read_text(encoding="utf-8") for path in files}


def saving_workflows() -> dict[str, str]:
    return {
        name: text
        for name, text in workflows().items()
        if any(f"ghti {command}" in text for command in SAVES_THE_TOKEN)
    }


def test_the_workflows_that_can_refresh_the_token_can_also_commit_it():
    found = saving_workflows()
    assert set(found) == {"check.yml", "reauth.yml", "reminder.yml", "sync.yml"}
    for name, text in found.items():
        assert "contents: write" in text, f"{name} can receive a new refresh token but cannot commit it"
        assert "git add -A state" in text, f"{name} can receive a new refresh token but never commits state/"
        assert "git push" in text, f"{name} commits the token without pushing it"


def test_every_workflow_that_writes_state_takes_the_same_lock():
    """Two runs committing state/ at the same time is a push that loses one of them."""
    for name, text in saving_workflows().items():
        assert "group: ghti-state" in text, f"{name} writes state/ outside the ghti-state lock"


def test_the_commit_step_runs_even_when_the_command_failed():
    """`status --fail-on-reminder` exits non-zero on purpose (code 5), and `sync` can
    fail halfway:
    the token it already refreshed still has to be saved."""
    for name, text in saving_workflows().items():
        if name == "reauth.yml":
            continue  # its commit step is guarded by redirect_url, checked below
        assert "if: always()" in text, f"{name} skips the commit step when the command fails"


def test_reauth_saves_only_when_an_address_was_pasted():
    text = workflows()["reauth.yml"]
    assert "if: inputs.redirect_url != ''" in text
    assert "if: inputs.redirect_url == ''" in text  # the run that only prints the link


def test_the_commands_the_workflows_call_still_exist():
    """A renamed command would leave the workflows calling something that is gone."""
    from ghti.cli import build_parser

    help_text = build_parser().format_help()
    for name, text in workflows().items():
        for command in SAVES_THE_TOKEN:
            if f"ghti {command}" in text:
                assert command in help_text, f"{name} calls `ghti {command}`, the CLI has no such command"
