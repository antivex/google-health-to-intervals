"""`ghti check`: before the first sync, say line by line what is ready and what is not.

Each line is one thing that can stop a sync: the three secrets, the time zone,
the intervals.icu key (one read-only request) and the Google authorization
(refreshed for real, so a check that passes means the next run will work).
Nothing is written to intervals.icu, no health value is read or printed.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from ghti.config import REQUIRED, Settings, config_dir, load_settings, raw_values
from ghti.errors import AuthError, AuthRequired, ConfigError, GhtiError
from ghti.intervals import IntervalsClient
from ghti.oauth import SCOPES, StoredToken
from ghti.sync import open_google

OK = "ok"
NOTE = "note"
MISSING = "MISSING"
PROBLEM = "PROBLEM"

# Exit codes, as in the CLI: 1 error, 2 configuration problem, 3 authorization needed.
# When more than one thing is wrong, the configuration problem is reported first:
# it is usually the cause of the others.
CODE_ORDER = (2, 1, 3)


@dataclass(frozen=True)
class Step:
    state: str
    title: str
    detail: str
    exit_code: int = 0

    @property
    def bad(self) -> bool:
        return self.state in (MISSING, PROBLEM)


def _scope_name(scope: str) -> str:
    return scope.rsplit(".", 2)[-2]


def _secrets() -> list[Step]:
    values = raw_values()
    missing = [key for key in REQUIRED if not values.get(key)]
    if missing:
        return [
            Step(
                MISSING,
                "Secrets",
                f"{', '.join(missing)} not set. On GitHub add them under Settings > Secrets and "
                f"variables > Actions; on your computer as environment variables or in "
                f"{config_dir() / 'config.env'}.",
                2,
            )
        ]
    return [Step(OK, "Secrets", "GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and INTERVALS_API_KEY are set.")]


def _timezone(settings: Settings) -> Step:
    if settings.timezone:
        return Step(OK, "Time zone", f"Days are cut at midnight in {settings.timezone}.")
    return Step(
        NOTE,
        "Time zone",
        "GHTI_TIMEZONE is not set, so the clock of the machine is used (on GitHub Actions that is "
        "UTC). Set it to your own zone, for example Europe/Rome, or last night's sleep can land on "
        "the wrong day.",
    )


def _intervals(settings: Settings, http: httpx.Client) -> Step:
    client = IntervalsClient(http, settings.intervals_api_key, settings.intervals_athlete_id)
    try:
        client.check_access(settings.today())
    except GhtiError as exc:
        return Step(PROBLEM, "intervals.icu", str(exc), 2)
    except httpx.HTTPError as exc:
        return Step(PROBLEM, "intervals.icu", f"Could not reach intervals.icu: {exc.__class__.__name__}: {exc}", 1)
    who = "the owner of the key" if settings.intervals_athlete_id == "0" else settings.intervals_athlete_id
    # The request above only proves the key can read. A key shared read-only
    # passes it and fails on the first PUT of the first sync, so ask for the
    # permission before promising that data will be written.
    permission = client.write_permission()
    if permission == "WRITE":
        return Step(OK, "intervals.icu", f"The key reads and may write athlete {who}.")
    if permission is None:
        return Step(
            NOTE,
            "intervals.icu",
            f"The key reads athlete {who}. Writing is not verified: intervals.icu did not say "
            "(no field icu_permission in the answer). The first sync will tell, and it stops at "
            "the first refused day without losing anything.",
        )
    return Step(
        PROBLEM,
        "intervals.icu",
        f"The key can read athlete {who} but not write to it (icu_permission {permission}). "
        f"A sync would fail at the first write. Check INTERVALS_ATHLETE_ID (now "
        f"{settings.intervals_athlete_id!r}; '0' means the key's owner) and, if the athlete is "
        f"someone else, ask them for write access on intervals.icu.",
        2,
    )


def _scopes(token: StoredToken) -> list[Step]:
    if not token.scopes:
        return []
    missing = [scope for scope in SCOPES if scope not in token.scopes]
    if not missing:
        return []
    names = ", ".join(_scope_name(scope) for scope in missing)
    return [
        Step(
            NOTE,
            "Permissions",
            f"Not granted: {names}. That data stays empty. Authorize again and tick every box.",
        )
    ]


def _google(settings: Settings, http: httpx.Client) -> list[Step]:
    try:
        _client, token, updated = open_google(settings, http)
    except AuthRequired as exc:
        return [Step(MISSING, "Google authorization", f"{exc} Run `ghti auth-url` and follow the two steps.", 3)]
    except AuthError as exc:
        return [Step(PROBLEM, "Google authorization", f"{exc} Authorize again: `ghti auth-url`.", 3)]
    except httpx.HTTPError as exc:
        return [Step(PROBLEM, "Google authorization", f"Could not reach Google: {exc.__class__.__name__}: {exc}", 1)]
    expires = token.expires_at.strftime("%Y-%m-%d %H:%M UTC")
    hours = int(token.remaining().total_seconds() // 3600)
    steps = [
        Step(
            OK,
            "Google authorization",
            f"Google accepted it. It {'expires' if token.expiry_known else 'should expire'} on {expires} (in {hours} h)."
            + (" The stored token was refreshed and saved." if updated else ""),
        )
    ]
    if hours <= 36 and token.expiry_known:
        steps.append(
            Step(NOTE, "Renewal", f"Fewer than 36 hours left ({hours} h). Renew now: `ghti auth-url`.")
        )
    elif hours <= 36:
        steps.append(
            Step(
                NOTE,
                "Renewal",
                f"The stored date says {hours} h, but Google never gave one: it is our 7-day guess. "
                "`ghti status` asks Google before asking you for anything.",
            )
        )
    return steps + _scopes(token)


def run_checks(http: httpx.Client) -> list[Step]:
    """Every check, in the order a first run meets them. Stops early only when
    a secret is missing: without it nothing else can be tried."""
    steps = _secrets()
    if steps[0].bad:
        return steps
    try:
        settings = load_settings()
    except ConfigError as exc:
        return steps + [Step(PROBLEM, "Settings", str(exc), 2)]
    steps.append(_timezone(settings))
    steps.append(_intervals(settings, http))
    return steps + _google(settings, http)


def exit_code(steps: list[Step]) -> int:
    codes = {step.exit_code for step in steps}
    for code in CODE_ORDER:
        if code in codes:
            return code
    return 0


def _verdict(steps: list[Step]) -> str:
    if any(step.bad for step in steps):
        return "Fix the lines marked MISSING or PROBLEM, then run the check again."
    if any(step.state == NOTE for step in steps):
        return "A sync can run now. The lines marked note are worth a minute of your time."
    return "Everything is ready: the next run will copy your data."


def text(steps: list[Step]) -> str:
    width = max(len(step.state) for step in steps)
    lines = ["Setup check"]
    for step in steps:
        lines.append(f"  {step.state.ljust(width)}  {step.title}: {step.detail}")
    return "\n".join(lines + ["", _verdict(steps)])


def markdown(steps: list[Step]) -> str:
    lines = ["## Setup check", ""]
    for step in steps:
        lines.append(f"- **{step.state}** {step.title}: {step.detail}")
    return "\n".join(lines + ["", _verdict(steps)]) + "\n"
