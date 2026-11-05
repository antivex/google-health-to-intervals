"""`ghti schedule`: the daily run, written out for this computer.

A sync copies the last three days and is safe to repeat, so one plain daily task
is enough: no service, no daemon, nothing of ours left running in the background.

This module only *prints* the task. It installs nothing, starts nothing and
changes no setting, so the person reads the exact commands before anything is
allowed near their machine. Two things decide whether a task that runs at night
will actually work, and both are checked here instead of being assumed:

- the way `ghti` is called. A scheduler does not have the shell's PATH, so the
  printed command uses absolute paths and never the bare word `ghti`;
- where the three secrets are. From `config.env` they are found by any task; from
  shell environment variables they are not, and the task would fail every night
  with a configuration error. In that case the text says so first.
"""

from __future__ import annotations

import re
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path

from ghti.config import config_dir, read_env_file
from ghti.errors import ConfigError
from ghti.github import help_line

TASK_NAME = "ghti-daily"
DEFAULT_AT = "07:30"
DEFAULT_DAYS = 3
PLATFORMS = ("windows", "macos", "linux")

_AT = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def parse_at(text: str) -> tuple[int, int]:
    """Read HH:MM, the way both schtasks and cron want it."""
    match = _AT.match(text.strip())
    if not match:
        raise ConfigError(f"--at wants a time like 07:30, not {text!r}.")
    return int(match.group(1)), int(match.group(2))


def this_platform(platform: str | None = None) -> str:
    name = platform or sys.platform
    if name in PLATFORMS:
        return name
    if name.startswith("win"):
        return "windows"
    if name == "darwin":
        return "macos"
    return "linux"


def runner() -> tuple[str, ...]:
    """How to call ghti from a scheduler: absolute, and valid without PATH.

    A scheduled task starts with none of the shell's PATH, so the bare word
    `ghti` is exactly what fails at 07:30 and works when tried by hand. The
    console script is preferred when it is really there; `python -m ghti` is the
    fallback that also works for an install without the scripts folder on PATH.
    """
    exe = Path(sys.executable)
    for name in ("ghti.exe", "ghti"):
        candidate = exe.parent / name
        if candidate.is_file():
            return (str(candidate),)
    return (str(exe), "-m", "ghti")


def quote(parts: tuple[str, ...] | list[str], platform: str) -> str:
    """Join a command for the shell that will run it, quoting only what needs it."""
    if platform == "windows":
        return " ".join(f'"{part}"' if " " in part else part for part in parts)
    return " ".join(shlex.quote(part) for part in parts)


def secrets_in_file() -> bool:
    """True when config.env holds the secrets, so a night task can find them."""
    values = read_env_file(config_dir() / "config.env")
    return all(values.get(key) for key in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "INTERVALS_API_KEY"))


@dataclass(frozen=True)
class Plan:
    platform: str
    hour: int
    minute: int
    days: int
    command: tuple[str, ...]
    log: Path
    secrets_ready: bool
    own: bool = True  # False when --os asks for a system that is not this one

    @property
    def at(self) -> str:
        return f"{self.hour:02d}:{self.minute:02d}"


def build(platform: str | None = None, at: str = DEFAULT_AT, days: int = DEFAULT_DAYS, log: str | None = None) -> Plan:
    if days < 1:
        raise ConfigError("--days wants at least 1.")
    hour, minute = parse_at(at)
    folder = config_dir()
    return Plan(
        platform=this_platform(platform),
        hour=hour,
        minute=minute,
        days=days,
        command=runner(),
        log=Path(log) if log else folder / "ghti-daily.log",
        secrets_ready=secrets_in_file(),
        own=this_platform(platform) == this_platform(),
    )


def _windows(plan: Plan) -> list[str]:
    wrapper = config_dir() / f"{TASK_NAME}.cmd"
    call = quote(plan.command, "windows")
    return [
        f"1. Save these three lines as {wrapper}",
        "",
        "   @echo off",
        f'   {call} status >> "{plan.log}" 2>&1',
        f'   {call} sync --days {plan.days} >> "{plan.log}" 2>&1',
        "",
        "2. Create the daily task (one line, in a normal window: no administrator):",
        "",
        f'   schtasks /Create /TN {TASK_NAME} /TR "{wrapper}" /SC DAILY /ST {plan.at}',
        "",
        "3. Check it is there, and try it now without waiting for tomorrow:",
        "",
        f"   schtasks /Query /TN {TASK_NAME}",
        f"   schtasks /Run /TN {TASK_NAME}",
        "",
        "To remove it:",
        "",
        f"   schtasks /Delete /TN {TASK_NAME} /F",
    ]


def _cron(plan: Plan) -> list[str]:
    call = quote(plan.command, plan.platform)
    log = shlex.quote(str(plan.log))
    line = (
        f"{plan.minute} {plan.hour} * * * "
        f"{call} status >> {log} 2>&1; "
        f"{call} sync --days {plan.days} >> {log} 2>&1"
    )
    sleeps = (
        "A laptop that is asleep at that hour skips the run; the next one copies the\n"
        "missed days anyway, because a sync always covers the last "
        f"{plan.days} days."
    )
    return [
        "1. Open your own crontab (it asks for no password and touches nothing else):",
        "",
        "   crontab -e",
        "",
        "2. Add this one line, save, close:",
        "",
        f"   {line}",
        "",
        "3. Check it is there, and try the command now without waiting for tomorrow:",
        "",
        "   crontab -l",
        f"   {call} sync --days {plan.days}",
        "",
        "To remove it: crontab -e again, delete that line, save.",
        "",
        *(
            [
                "macOS can ask for permission the first time a scheduled command runs:",
                "allow it, or the run is skipped without saying anything.",
                "",
            ]
            if plan.platform == "macos"
            else []
        ),
        *sleeps.splitlines(),
    ]


def text(plan: Plan) -> str:
    """The whole page: what will run, what it needs, how to undo it."""
    head = [
        f"A daily run at {plan.at}, on {plan.platform}. Nothing below has been done:",
        "these are the commands to run, once, if you want them.",
        "",
        f"What it will do every day: copy the last {plan.days} days to intervals.icu and",
        f"append what happened to {plan.log}",
        "Repeating a day is harmless: the same values overwrite the same day.",
        "",
    ]
    if not plan.secrets_ready:
        head += [
            "First, one thing that would make every night fail:",
            "",
            f"   your three secrets are not in {config_dir() / 'config.env'}.",
            "   A scheduled task does not see the variables you export in a terminal,",
            "   so it would stop with a configuration error every day. Write them in",
            "   that file first (one KEY=VALUE per line), then run `ghti check`.",
            "",
        ]
    if not plan.own:
        head += [
            f"The paths below are the ones of this computer, not of the {plan.platform} one:",
            "on that machine run `ghti schedule` again to get its own.",
            "",
        ]
    body = _windows(plan) if plan.platform == "windows" else _cron(plan)
    tail = [
        "",
        "The log is plain text and holds no values, only counts and errors.",
        "The first line of each run says when the Google authorization expires, so a",
        "token about to die shows up in the log before a sync fails.",
        f"Something broken? {help_line()}",
    ]
    return "\n".join(head + body + tail) + "\n"


def other_platforms(plan: Plan) -> str:
    names = [name for name in PLATFORMS if name != plan.platform]
    return f"For another computer: ghti schedule --os {' | --os '.join(names)}"
