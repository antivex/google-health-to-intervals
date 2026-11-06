"""Output helpers: GitHub Actions job summary, annotations and the
"please authorize again" instructions."""

from __future__ import annotations

import os
import sys

from ghti.oauth import REDIRECT_URI
from ghti.sync import PARTIAL as PARTIAL_CODE

UPSTREAM = "https://github.com/antivex/google-health-to-intervals"
GUIDE_URL = "https://antivex.gumroad.com/l/fitbit-google-health-intervals"
ERRORS_ANCHOR = "#error-messages-one-by-one"
SETUP_ANCHOR = "#setup-about-20-30-minutes-once"


def in_actions() -> bool:
    return os.environ.get("GITHUB_ACTIONS") == "true"


def write_summary(markdown: str) -> None:
    """Append to the job summary (shown on the workflow run page)."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(markdown.rstrip() + "\n\n")


def help_footer() -> str:
    """Where to look next when a run failed. Goes under the error message, never instead of it."""
    lines = [
        "### Stuck on this error?",
        "",
        f"1. [Error messages, one by one]({UPSTREAM}{ERRORS_ANCHOR}): every message this bridge can",
        "   print, with the fix. Look for the first line of the error above.",
        f"2. If yours is not there, [open an issue]({UPSTREAM}/issues/new) with the text above. It is",
        "   free, the error text carries no health data, and the fix then helps everybody.",
        f"3. The [paid guide]({GUIDE_URL}) (mine, optional) answers a different question: which route",
        "   fits you (Health Connect, Health Sync, Apple Health, CSV, the intervals.icu API or this",
        "   bridge) and how to fill the gap since 2 November. You do not need it to fix this error.",
    ]
    return "\n".join(lines) + "\n"


def help_line() -> str:
    """The same pointer in one line, for a terminal."""
    return (
        f"Error messages with the fix: {UPSTREAM}{ERRORS_ANCHOR}"
        f" - not there? Open an issue: {UPSTREAM}/issues/new"
    )


def warning(message: str) -> None:
    if in_actions():
        print(f"::warning::{message}")
    else:
        print(f"Warning: {message}", file=sys.stderr)


def mask_in_logs(value: str) -> None:
    """Ask GitHub to hide a value in the following log lines."""
    if in_actions() and value:
        print(f"::add-mask::{value}")


def _workflow_url(file_name: str) -> str | None:
    """The run page of one of our workflows, or None outside GitHub Actions."""
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        return None
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    return f"{server}/{repo}/actions/workflows/{file_name}"


def _reauth_workflow_url() -> str | None:
    return _workflow_url("reauth.yml")


def resume_instructions(resume_from: str, command: str) -> str:
    """What to do about a gap, written for whoever is reading the run page.

    The error text already carries the `ghti backfill` line that ends the gap.
    That line is a terminal command, and the buyer who installed this bridge
    with "Use this template" has no terminal: the run page is the only place
    they ever see. So the cloud route - Actions > Sync > Run workflow, with the
    day in `from` - comes first, and the command stays for whoever has a PC.

    The title says "stopped part-way", not "error": a run that wrote 90 of 200
    days must not read like a run that wrote nothing. That is the whole point
    of exit code 7 being its own code, and the run page said `## Error` for
    both until 6 November 2026.
    """
    workflow = _workflow_url("sync.yml")
    lines = [
        "## Stopped part-way: the days it got through are on intervals.icu, the rest are not",
        "",
        "Nothing fills this gap on its own: the next scheduled sync copies the last few days",
        f"and never looks back. Carry on from **{resume_from}**, whichever way suits you:",
        "",
    ]
    if workflow:
        lines += [
            f"- **Here on GitHub, no computer needed:** open [Actions > Sync]({workflow}), click",
            f"  **Run workflow**, type `{resume_from}` into **from** and click the green",
            "  **Run workflow** button. It copies that day up to today.",
        ]
    lines += [
        f"- **On a computer:** `{command}`",
        "",
        f"Until those days are on intervals.icu, `ghti status` exits {PARTIAL_CODE}: the same code this run",
        "exited with, so a scheduled check stays red instead of going green over a gap.",
    ]
    return "\n".join(lines) + "\n"


def auth_instructions(link: str, title: str, reason: str = "") -> str:
    """Markdown steps to (re)authorize, for GitHub or for a local terminal."""
    workflow = _reauth_workflow_url()
    if workflow:
        last_step = (
            f"3. Open [Actions > Re-authorize]({workflow}), click **Run workflow**, paste the "
            "address into **redirect_url** and click the green **Run workflow** button."
        )
    else:
        last_step = '3. Run: `ghti auth-finish "<the address you copied>"`'
    lines = [f"## {title}", ""]
    if reason:
        lines += [reason, ""]
    lines += [
        'Google asks personal apps in "Testing" mode to be approved again every 7 days. It takes a minute:',
        "",
        f"1. Open this link and approve access: **[Authorize Google Health]({link})**",
        "   Google warns that the app has not been verified: it is your own app, so click *Continue*.",
        '2. At the end the browser shows an error such as "This site can\'t be reached". That is expected.',
        f"   Copy the **whole address** from the address bar. It starts with `{REDIRECT_URI}?state=`.",
        last_step,
        "",
        "Do steps 2 and 3 within a few minutes: the code in the address expires quickly.",
        "The days missed in the meantime are synced right after.",
    ]
    return "\n".join(lines) + "\n"
