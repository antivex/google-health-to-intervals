"""Command line: ghti demo | check | sync | backfill | auth-url | auth-finish | export-csv | schedule | status.

Exit codes: 0 ok, 1 error, 2 configuration problem (or no command given),
3 Google authorization needed. Three commands say something that is not a
failure, so they have codes of their own, outside those three: 4 (`gaps`) some
days are missing on intervals.icu, 5 (`status --fail-on-reminder`) the Google
authorization needs renewing soon, 6 (`check`) nothing is broken and a step of
yours is simply not done yet.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import httpx

from ghti import __version__
from ghti import check as check_mod
from ghti.check import exit_code as check_exit_code
from ghti.check import markdown as check_markdown
from ghti.check import run_checks
from ghti.check import text as check_text
from ghti.config import (
    INTERVALS_ONLY,
    REQUIRED,
    is_configured,
    load_settings,
    missing_settings,
    raw_values,
    redact,
    utcnow,
)
from ghti.crypto import Keys
from ghti.errors import AuthRequired, ConfigError, GhtiError, describe
from ghti.examples import readings as example_readings
from ghti import journal
from ghti.github import (
    SETUP_ANCHOR,
    UPSTREAM,
    auth_instructions,
    help_footer,
    help_line,
    in_actions,
    mask_in_logs,
    warning,
    write_summary,
)
from ghti.oauth import (
    DEFAULT_REFRESH_LIFETIME,
    REDIRECT_URI,
    StoredToken,
    TokenStore,
    build_auth_url,
    exchange_code,
    parse_redirect_url,
    refresh_access_token,
)
from ghti.schedule import DEFAULT_AT, DEFAULT_DAYS, PLATFORMS
from ghti.schedule import build as build_schedule
from ghti.schedule import other_platforms
from ghti.schedule import text as schedule_text
from ghti.mapping import merge, to_wellness_records
from ghti.sync import SyncReport, export_csv, run_sync
from ghti import gaps as gaps_mod
from ghti.intervals import IntervalsClient

MAX_DAYS = 3650


def _http() -> httpx.Client:
    return httpx.Client(timeout=30.0, headers={"User-Agent": f"google-health-to-intervals/{__version__}"})


def _keys(settings) -> Keys:
    """The pair that encrypts the stored token: it needs the Google secret."""
    return Keys(settings.google()[1], settings.intervals_api_key)


def _day(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a date in the form YYYY-MM-DD") from None


def _auth_text(link: str) -> str:
    """Plain-text instructions for a terminal."""
    return (
        "Open this link in your browser and approve access:\n\n"
        f"{link}\n\n"
        "Google warns that the app has not been verified: it is your own app, click Continue.\n"
        'At the end the browser shows an error ("This site can\'t be reached"). That is expected.\n'
        f"Copy the whole address from the address bar (it starts with {REDIRECT_URI}?state=)\n"
        'and, within a few minutes, run:  ghti auth-finish "<the address>"\n'
    )


def _show_auth(settings, title: str, reason: str = "") -> None:
    link = build_auth_url(settings, _keys(settings), encode_client_id=in_actions())
    if in_actions():
        markdown = auth_instructions(link, title, reason)
        write_summary(markdown)
        print(markdown)
    else:
        print((reason + "\n\n" if reason else "") + _auth_text(link))


def _report(report: SyncReport, show_values: bool) -> int:
    print(report.text(show_values))
    write_summary(report.markdown(show_values))
    for name, error in sorted(report.failed.items()):
        warning(f"{name}: {error}")
    if report.skipped:
        warning("Not authorized: " + ", ".join(sorted(report.skipped)) + ". Tick every box at the next authorization.")
    if report.token_updated:
        print("The stored Google token was updated.")
    return 0


def _run_and_record(
    command: str, settings, start: date, end: date, dry_run: bool, confirm: bool = True
) -> SyncReport:
    """Run a sync and write one diary line, so `ghti status` can report it later.

    A failure is written too: without it a scheduled run that stops working
    leaves no trace the user can read the next morning.
    """
    try:
        with _http() as http:
            report = run_sync(settings, start, end, http, dry_run=dry_run, confirm=confirm)
    except GhtiError as error:
        if not dry_run:
            journal.append(
                journal.failure(
                    str(error),
                    command=command,
                    start=start,
                    end=end,
                    # A break part-way carries these; any other failure wrote nothing.
                    sent=getattr(error, "written", 0),
                    resume_from=getattr(error, "resume_from", None),
                )
            )
        raise
    if not dry_run:
        journal.append(journal.from_report(report, command=command))
    return report

# --- commands ------------------------------------------------------------------


def _settings_or_record(command: str, dry_run: bool = False):
    """Load the settings, and write a diary line if they are not usable.

    `_run_and_record` only sees failures raised inside the sync, so a run that
    dies here left no trace at all: the next morning `ghti status` said nothing
    had ever run and blamed the daily task, which had fired on time. This is
    the failure `ghti schedule` warns about - secrets exported in a terminal
    are invisible to a scheduled task - so it is the one that must be readable.
    """
    try:
        return load_settings()
    except GhtiError as error:
        if not dry_run:
            journal.append(journal.failure(str(error), command=command))
        raise


def _unconfigured(args) -> bool:
    """The template repository itself has no secrets: its scheduled runs do nothing."""
    if not args.skip_if_unconfigured or is_configured():
        return False
    message = "Not configured yet: add the repository secrets described in the README. Nothing to do."
    print(message)
    write_summary("## Google Health to intervals.icu\n\n" + message)
    return True


def cmd_sync(args) -> int:
    if _unconfigured(args):
        return 0
    if not 1 <= args.days <= MAX_DAYS:
        raise ConfigError(f"--days must be between 1 and {MAX_DAYS}.")
    settings = _settings_or_record("sync", args.dry_run)
    end = settings.today()
    start = end - timedelta(days=args.days - 1)
    report = _run_and_record("sync", settings, start, end, args.dry_run, confirm=args.confirm)
    return _report(report, args.show_values)


def cmd_backfill(args) -> int:
    settings = _settings_or_record("backfill", args.dry_run)
    end = args.to or settings.today()
    if args.start > end:
        raise ConfigError(f"--from ({args.start}) is after --to ({end}).")
    if (end - args.start).days >= MAX_DAYS:
        raise ConfigError(f"The range is longer than {MAX_DAYS} days.")
    report = _run_and_record("backfill", settings, args.start, end, args.dry_run, confirm=args.confirm)
    return _report(report, args.show_values)


def cmd_auth_url(args) -> int:
    _show_auth(load_settings(), "Authorize Google Health")
    return 0


def cmd_auth_finish(args) -> int:
    settings = load_settings()
    address = args.url or os.environ.get("GHTI_REDIRECT_URL", "")
    if not address.strip():
        raise ConfigError('Paste the address from the browser: ghti auth-finish "http://127.0.0.1:8765/?state=..."')
    with contextlib.suppress(GhtiError):  # exchange_code below explains a bad address
        mask_in_logs(parse_redirect_url(address)[0])  # hide the one-time code in GitHub logs
    keys = _keys(settings)
    with _http() as http:
        token, missing = exchange_code(settings, keys, address, http)
    TokenStore(settings.token_file, keys).save(token)
    expires = token.expires_at.strftime("%Y-%m-%d %H:%M UTC")
    message = f"Google authorization saved. It expires on {expires}."
    print(message)
    if not in_actions():
        print(f"Token file: {settings.token_file}")
    summary = f"## Google authorization saved\n\n{message}\n"
    if missing:
        names = ", ".join(scope.rsplit(".", 2)[-2] for scope in missing)
        warning(f"Some permissions were not granted ({names}): that data will not be copied.")
        summary += f"\nNot granted: {names}. Authorize again and tick every box to copy that data.\n"
    write_summary(summary)
    return 0


def cmd_export_csv(args) -> int:
    settings = load_settings()
    end = args.to or settings.today()
    start = args.start or end - timedelta(days=args.days - 1)
    if start > end:
        raise ConfigError(f"--from ({start}) is after --to ({end}).")
    with _http() as http:
        text, report = export_csv(settings, start, end, http)
    if args.output == "-":
        sys.stdout.write(text)
    else:
        Path(args.output).write_text(text, encoding="utf-8", newline="")
        print(f"Wrote {len(report.days)} day(s) to {args.output}")
    for name, error in sorted(report.failed.items()):
        warning(f"{name}: {error}")
    return 0


DEMO_NOTE = (
    "Example data, invented (no Google account, no network, nothing is sent).\n"
    "It shows what a real sync would write to intervals.icu, and how the awkward\n"
    "cases are handled: a nap is ignored, the later weigh-in of a day wins, a night\n"
    "belongs to the day you wake up, and a day with 0 steps is left alone."
)


def cmd_demo(args) -> int:
    """Show the output of a sync on example data: no setup, no credentials, no network."""
    days = merge(example_readings())
    records = to_wellness_records(days)
    report = SyncReport(
        start=min(days),
        end=max(days),
        days=days,
        records_sent=len(records),
        dry_run=True,
    )
    # With --json the words go to stderr and stdout carries the records alone, so
    # `ghti demo --json | jq` (or any other reader) gets JSON and nothing else. On a
    # terminal both still show, in the same order as before.
    words = sys.stderr if args.json else sys.stdout
    print(DEMO_NOTE, file=words)
    print(file=words)
    print(report.text(show_values=True), file=words)
    if args.json:
        print(file=words)
        print("What intervals.icu would receive (PUT /wellness-bulk):", file=words)
        print(json.dumps(records, indent=2, sort_keys=True))
    return 0


def cmd_check(args) -> int:
    """Say what is ready and what is missing, without writing anything.

    Exit codes: `check.WAITING` (6) when every bad line is only waiting for a
    step of the person (secrets, Google authorization), and the ordinary 1/2/3
    as soon as one line is a real problem. See `check.WAITING` for the
    measurement behind the difference.
    """
    with _http() as http:
        steps = run_checks(http)
    print(check_text(steps))
    write_summary(check_markdown(steps))
    code = check_exit_code(steps)
    if code and in_actions():
        print(help_footer())
        write_summary(help_footer())
    elif code:
        print(help_line(), file=sys.stderr)
    return code


def cmd_schedule(args) -> int:
    """Print the daily task for this computer. Installs nothing, writes nothing."""
    plan = build_schedule(platform=args.os, at=args.at, days=args.days, log=args.log)
    print(schedule_text(plan), end="")
    print()
    print(other_platforms(plan))
    return 0


def _ask_google_if_alive(settings, token: StoredToken) -> StoredToken:
    """The stored date has passed, but Google never said the token expires.

    Google only sends `refresh_token_expires_in` when there is an expiry (a project
    whose publishing status is "Testing"); without it our date is a guess, so a run
    must not stop on it. Ask Google instead: if the refresh works, the authorization
    is alive and the guess moves on. refresh_access_token raises AuthRequired when
    Google really refuses it, with the usual "authorize again" message.
    """
    with httpx.Client(timeout=30) as http:
        _access, new_refresh = refresh_access_token(settings, token, http)
    alive = StoredToken(
        refresh_token=new_refresh or token.refresh_token,
        obtained_at=token.obtained_at,
        expires_at=utcnow() + DEFAULT_REFRESH_LIFETIME,
        scopes=token.scopes,
        expiry_known=False,
    )
    TokenStore(settings.token_file, _keys(settings)).save(alive)
    print(
        "Google never gave an expiry date for this authorization, and the 7-day guess "
        "has run out: asked Google just now and the authorization still works. "
        "Checking again in 7 days; nothing for you to do."
    )
    return alive


def cmd_gaps(args) -> int:
    """Ask intervals.icu which days of a range it has nothing of ours on.

    `ghti status` reports what this computer tried to send; it cannot see a
    backfill finished by hand, a day the watch never recorded, or a day lost
    on the way. Without `--from` the range is the gap the diary is still
    complaining about, which is the case that sends people here: the answer is
    often that most of it is already there and the command to finish it is
    much shorter than the one `ghti status` prints.

    Exit codes, because this command is meant to be an alarm: 0 nothing is
    missing, `gaps.FOUND` (4) some days are, and the ordinary 1/2/3 when the
    check did not run at all. 4 is not 1 on purpose - when a wrong key gives
    HTTP 401 a scheduled task must not read it as "those days are gone", and
    must not read it as a clean answer either.

    This reads intervals.icu and nothing else, so it asks only for
    INTERVALS_API_KEY. Somebody who wants to know what is already on the site
    should not have to set up a Google authorization first - that is the setup
    a broken backfill makes people doubt, and this is the command that answers
    the doubt.
    """
    settings = load_settings(needs=INTERVALS_ONLY)
    end = args.to or settings.today()
    if args.start:
        start = args.start
    else:
        unfinished = journal.unfinished()
        gap = unfinished.gap if unfinished else None
        if gap:
            start, end = gap[0], (args.to or gap[1])
            print(f"Checking the range the diary says is unfinished: {start} to {end}.")
        else:
            start = end - timedelta(days=args.days - 1)
    if start > end:
        raise ConfigError(f"--from ({start}) is after --to ({end}).")
    if (end - start).days >= MAX_DAYS:
        raise ConfigError(f"The range is longer than {MAX_DAYS} days.")
    cost = gaps_mod.requests_for(start, end)
    print(f"Reading {(end - start).days + 1} day(s) back from intervals.icu ({cost} request(s))...")
    with _http() as http:
        client = IntervalsClient(http, settings.intervals_api_key, settings.intervals_athlete_id)
        ranges = gaps_mod.missing(client, start, end)
    text = gaps_mod.text(ranges, start, end)
    print(text)
    write_summary("## Days missing on intervals.icu\n\n```\n" + text + "\n```")
    return gaps_mod.FOUND if ranges else 0


#: What `ghti status --fail-on-reminder` exits with when it is only reminding.
#
# Measured on 6 November 2026, with the flag on: the reminder of day 6 ("the
# Google authorization expires tomorrow", authorization still alive) exited 1,
# and a status that could not even ask Google (dead network) also exited 1. Two
# opposite facts, one code: a scheduled task reading 1 could not tell "renew it
# tomorrow" from "this check never ran".
#
# So the reminder gets a code of its own, outside the three the errors use
# everywhere (1 any failure, 2 bad setup, 3 Google authorization needed) and
# different from `gaps.FOUND` (4), which already means "days are missing", and
# from `check.WAITING` (6), "the setup is still waiting for you". Any non-zero
# still turns the GitHub run red and still sends the email, so
# .github/workflows/reminder.yml keeps working exactly as before.
REMINDING = 5


def cmd_status(args) -> int:
    if _unconfigured(args):
        return 0
    if not in_actions():
        # On GitHub the runner is new every time, so the diary is always empty there.
        runs = journal.read()
        print(
            journal.summary(
                runs[-1] if runs else None,
                setup_ok=not missing_settings(),
                unfinished_run=journal.unfinished(runs=runs),
            )
        )
    settings = load_settings()
    token = TokenStore(settings.token_file, _keys(settings)).load()
    remaining = token.remaining()
    if remaining <= timedelta(0) and not token.expiry_known:
        token = _ask_google_if_alive(settings, token)
        remaining = token.remaining()
    expires = token.expires_at.strftime("%Y-%m-%d %H:%M UTC")
    if remaining <= timedelta(0):
        raise AuthRequired(f"The Google authorization expired on {expires}.")
    hours = int(remaining.total_seconds() // 3600)
    word = "expires" if token.expiry_known else "should expire"
    print(f"Google authorization from {token.obtained_at:%Y-%m-%d}, {word} on {expires} (in {hours} h).")
    if remaining <= timedelta(hours=args.remind_hours):
        when = "today" if hours < 12 else "tomorrow"
        guess = "" if token.expiry_known else (
            " (Google did not give a date for this one, so this is the 7-day guess of a"
            ' project in "Testing" mode: if yours no longer expires, the sync keeps going'
            " by itself and this reminder stops being needed.)"
        )
        _show_auth(
            settings,
            f"Reminder: the Google authorization {'expires' if token.expiry_known else 'may expire'} {when}",
            f"It {word} on {expires} (in about {hours} hours). Renew it now and the sync will not stop." + guess,
        )
        return REMINDING if args.fail_on_reminder else 0
    write_summary(f"## Google authorization OK\n\nIt {word} on {expires} (in {hours} hours).")
    return 0


# --- where to start -------------------------------------------------------------

SETUP_URL = f"{UPSTREAM}{SETUP_ANCHOR}"

# The order below is the only order that works: step 1 runs on a bare clone, steps 2
# to 4 do not, and step 5 is worth reading only once step 4 has worked by hand.
# ORDER is what the tests check against the parser, so a renamed command cannot
# leave this page pointing at nothing.
ORDER = ("demo", "check", "auth-url", "auth-finish", "sync", "schedule")


def start_here() -> str:
    """What to do first, for someone who just typed `ghti` and nothing else."""
    lines = [
        "ghti copies Google Health (Fitbit, Pixel Watch) wellness data to intervals.icu.",
        "",
        "Start here, in this order:",
        "",
        "  1. ghti demo          see what a sync writes, on example data.",
        "                        Needs nothing: no Google account, no key, no network.",
        "  2. ghti check         check your own setup. Reads only, sends nothing.",
        "  3. ghti auth-url      authorize Google in the browser (first time only),",
        '     ghti auth-finish "<the address the browser ends on>"',
        "  4. ghti sync          copy the last 3 days. Safe to repeat.",
        "  5. ghti schedule      let it run on its own, every day. Prints the task for",
        "                        your system and installs nothing.",
        "",
        "Steps 2 to 4 need the Google Cloud setup: free, about 20-30 minutes, once.",
        f"  {SETUP_URL}",
        "Step 1 needs none of it.",
        "",
        "Installed with uv? Put `uv run` in front: uv run ghti demo",
        "",
        "Every command, with its options: ghti --help",
        f"Something broken? {help_line()}",
    ]
    return "\n".join(lines) + "\n"


# --- parser and entry point ------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ghti",
        description="Copy Google Health (Fitbit, Pixel Watch) wellness data to intervals.icu.",
        epilog=(
            "start here: ghti demo (needs nothing) -> ghti check -> ghti auth-url and\n"
            'ghti auth-finish "<address>" -> ghti sync -> ghti schedule\n'
            f"the setup steps 2 to 4 need (free, once): {SETUP_URL}\n"
            "run ghti with no command for the same thing, spelled out."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    # Not required: `ghti` alone used to be an argparse error that said nothing about
    # where to start, and it is the first thing a person types after cloning.
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("demo", help="show what a sync writes, on example data: no setup, no network")
    p.add_argument("--json", action="store_true", help="also print the records intervals.icu would receive")
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser(
        "check",
        help="check the setup before the first sync; writes nothing,"
        f" exits {check_mod.WAITING} when a step of yours is still missing,"
        " 1/2/3 when something is broken",
        description="Say line by line what is ready and what is not, before the first sync."
        " It writes nothing to intervals.icu and prints no health value."
        f" Exit codes, for a scheduled task: 0 everything is ready, {check_mod.WAITING} when"
        " nothing is broken and a step of yours is simply not done yet (no secrets, no Google"
        " authorization, or one to renew), 2 when something you set is refused or a setting is"
        " wrong, 3 when only the Google side refuses, 1 when a check could not run (network)."
        f" {check_mod.WAITING} is not 1/2/3 on purpose: a setup still to finish must not arrive"
        " as a setup that broke.",
    )
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("sync", help="copy the last N days (default 3); safe to repeat")
    p.add_argument("--days", type=int, default=3)
    p.add_argument("--dry-run", action="store_true", help="read from Google but do not write to intervals.icu")
    p.add_argument(
        "--no-confirm",
        dest="confirm",
        action="store_false",
        help="do not read the days back from intervals.icu to check they were saved",
    )
    p.add_argument("--show-values", action="store_true", help="print the values (avoid in public logs)")
    p.add_argument("--skip-if-unconfigured", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("backfill", help="copy a past period")
    p.add_argument("--from", dest="start", type=_day, required=True, metavar="YYYY-MM-DD")
    p.add_argument("--to", type=_day, metavar="YYYY-MM-DD", help="default: today")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--no-confirm",
        dest="confirm",
        action="store_false",
        help="do not read the days back from intervals.icu to check they were saved",
    )
    p.add_argument("--show-values", action="store_true")
    p.set_defaults(func=cmd_backfill)

    p = sub.add_parser("auth-url", help="print the Google consent link")
    p.set_defaults(func=cmd_auth_url)

    p = sub.add_parser("auth-finish", help="save the authorization from the browser address")
    p.add_argument("url", nargs="?", help="the full address from the browser (or env GHTI_REDIRECT_URL)")
    p.set_defaults(func=cmd_auth_finish)

    p = sub.add_parser("export-csv", help="write the data as CSV instead of sending it")
    p.add_argument("--from", dest="start", type=_day, metavar="YYYY-MM-DD")
    p.add_argument("--to", type=_day, metavar="YYYY-MM-DD", help="default: today")
    p.add_argument("--days", type=int, default=30, help="used when --from is not given (default 30)")
    p.add_argument("--output", "-o", default="-", help="file name, or - for the screen (default)")
    p.set_defaults(func=cmd_export_csv)

    p = sub.add_parser(
        "schedule",
        help="print the daily task for this computer; installs nothing, writes nothing",
    )
    p.add_argument("--at", default=DEFAULT_AT, metavar="HH:MM", help=f"when to run (default {DEFAULT_AT})")
    p.add_argument("--days", type=int, default=DEFAULT_DAYS, help=f"days per run (default {DEFAULT_DAYS})")
    p.add_argument("--os", choices=PLATFORMS, help="show it for another system (default: this one)")
    p.add_argument("--log", help="log file (default: one inside the ghti config folder)")
    p.set_defaults(func=cmd_schedule)

    p = sub.add_parser(
        "gaps",
        help="ask intervals.icu which days it has nothing of ours on; writes nothing,"
        f" needs only INTERVALS_API_KEY; exits {gaps_mod.FOUND} when days are missing,"
        " 0 when none are, 1/2/3 when the check failed",
        description="Ask intervals.icu which days of a range it has nothing of ours on."
        " It writes nothing and needs only INTERVALS_API_KEY."
        f" Exit codes, for a scheduled task that uses it as an alarm: {gaps_mod.FOUND} when days"
        " are missing, 0 when none are, 1 when the check failed (wrong key, network),"
        " 2 when a setting is missing."
        f" {gaps_mod.FOUND} is not 1 on purpose: a check that never ran must not sound like"
        " missing days, nor like a clean answer.",
    )
    p.add_argument("--from", dest="start", type=_day, metavar="YYYY-MM-DD", help="default: the gap the diary reports")
    p.add_argument("--to", type=_day, metavar="YYYY-MM-DD", help="default: today")
    p.add_argument("--days", type=int, default=30, help="used when neither --from nor a diary gap gives a start (default 30)")
    p.set_defaults(func=cmd_gaps)

    p = sub.add_parser(
        "status",
        help="show the last run and when the Google authorization expires",
        description="Show the last run from the diary and when the Google authorization expires."
        " Exit codes: 0 all good (and 0 for the reminder too, unless --fail-on-reminder),"
        f" {REMINDING} with --fail-on-reminder when it is only reminding you to renew,"
        " 3 when the authorization has really expired, 1 when the check could not run"
        " (network), 2 when a setting is missing."
        f" {REMINDING} is not 1 on purpose: a scheduled task must not read a reminder about"
        " tomorrow as a check that never happened.",
    )
    p.add_argument("--remind-hours", type=float, default=36, help="remind when fewer hours are left (default 36)")
    p.add_argument(
        "--fail-on-reminder",
        action="store_true",
        help=f"exit with code {REMINDING} when reminding (a red GitHub run, so you get the email)",
    )
    p.add_argument("--skip-if-unconfigured", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_status)
    return parser


def _secrets() -> tuple[str, ...]:
    values = raw_values()
    return tuple(values.get(key, "") for key in REQUIRED if key != "GOOGLE_CLIENT_ID")


def _report_error(message: str) -> None:
    """Print the error and say where the answer is: FAQ, issue, paid guide."""
    print(f"Error: {message}", file=sys.stderr)
    print(help_line(), file=sys.stderr)
    write_summary(f"## Error\n\n{message}\n\n{help_footer()}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "command", None) is None:
        print(start_here(), end="")
        return 2
    try:
        return args.func(args)
    except AuthRequired as exc:
        try:
            _show_auth(load_settings(), "Google authorization needed", str(exc))
        except ConfigError:
            print(str(exc), file=sys.stderr)
        return exc.exit_code
    except GhtiError as exc:
        message = redact(str(exc), _secrets())
        _report_error(message)
        return exc.exit_code
    except httpx.HTTPError as exc:
        message = redact(describe(exc), _secrets())
        _report_error(message)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
