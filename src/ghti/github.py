"""Output helpers: GitHub Actions job summary, annotations and the
"please authorize again" instructions."""

from __future__ import annotations

import os
import sys

from ghti.oauth import REDIRECT_URI

UPSTREAM = "https://github.com/antivex/google-health-to-intervals"
GUIDE_URL = "https://antivex.gumroad.com/l/fitbit-google-health-intervals"


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
        f"1. The [FAQ]({UPSTREAM}#faq) covers the most frequent ones: missing values, the browser",
        "   error during authorization, secrets changed after the last authorization.",
        f"2. If it is not there, [open an issue]({UPSTREAM}/issues/new) with the text above. It is",
        "   free, the error text carries no health data, and the fix then helps everybody.",
        f"3. The [paid guide]({GUIDE_URL}) (mine, optional) lists this bridge's error messages one",
        "   by one with the fix, and compares the other routes in case this one is not the right",
        "   tool for you.",
    ]
    return "\n".join(lines) + "\n"


def help_line() -> str:
    """The same pointer in one line, for a terminal."""
    return f"FAQ: {UPSTREAM}#faq - still stuck? Open an issue: {UPSTREAM}/issues/new"


def warning(message: str) -> None:
    if in_actions():
        print(f"::warning::{message}")
    else:
        print(f"Warning: {message}", file=sys.stderr)


def mask_in_logs(value: str) -> None:
    """Ask GitHub to hide a value in the following log lines."""
    if in_actions() and value:
        print(f"::add-mask::{value}")


def _reauth_workflow_url() -> str | None:
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        return None
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    return f"{server}/{repo}/actions/workflows/reauth.yml"


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
