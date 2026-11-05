"""A short diary of the runs, so a sync that runs on its own can be checked later.

Every real sync (not `--dry-run`) appends one line to a small JSON-lines file.
`ghti status` reads the last line and says when the last run happened, how many
days it sent, and whether it failed. If nothing has run for a few days it says
so: that is the only way to notice that a scheduled task stopped firing.

The diary holds dates, counts, metric names and error messages only. No values
and no secrets ever reach it, so it is safe to attach to a bug report.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from ghti import config
from ghti.config import config_dir, raw_values

KEEP = 200  # lines kept in the file; older runs are dropped
STALE_AFTER = timedelta(days=2)  # after this, say that the daily run may be stopped


def journal_file() -> Path:
    """Where the diary lives (GHTI_JOURNAL_FILE wins, as for the token file)."""
    override = raw_values().get("GHTI_JOURNAL_FILE", "").strip()
    return Path(override) if override else config_dir() / "runs.jsonl"


@dataclass(frozen=True)
class Run:
    at: datetime
    command: str = "sync"
    start: date | None = None
    end: date | None = None
    sent: int = 0
    failed: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and not self.failed

    def as_dict(self) -> dict:
        return {
            "at": self.at.astimezone(UTC).isoformat(timespec="seconds"),
            "command": self.command,
            "start": self.start.isoformat() if self.start else None,
            "end": self.end.isoformat() if self.end else None,
            "sent": self.sent,
            "failed": list(self.failed),
            "skipped": list(self.skipped),
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Run:
        def day(value) -> date | None:
            return date.fromisoformat(value) if isinstance(value, str) and value else None

        return cls(
            at=datetime.fromisoformat(data["at"]),
            command=str(data.get("command") or "sync"),
            start=day(data.get("start")),
            end=day(data.get("end")),
            sent=int(data.get("sent") or 0),
            failed=tuple(data.get("failed") or ()),
            skipped=tuple(data.get("skipped") or ()),
            error=str(data.get("error") or ""),
        )


def from_report(report, command: str = "sync") -> Run:
    """Turn a SyncReport into one diary line."""
    return Run(
        at=config.utcnow(),
        command=command,
        start=report.start,
        end=report.end,
        sent=report.records_sent,
        failed=tuple(sorted(report.failed)),
        skipped=tuple(sorted(report.skipped)),
    )


def failure(message: str, command: str = "sync", start: date | None = None, end: date | None = None) -> Run:
    """A run that could not finish: the message is what the user was shown."""
    return Run(at=config.utcnow(), command=command, start=start, end=end, error=message)


def append(run: Run, path: Path | None = None) -> Path:
    """Add one line, keeping the file short. Never raises: a diary is not the job."""
    path = path or journal_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(run.as_dict(), ensure_ascii=False) + "\n")
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) > KEEP:
            path.write_text("\n".join(lines[-KEEP:]) + "\n", encoding="utf-8")
    except OSError:
        pass
    return path


def read(path: Path | None = None) -> list[Run]:
    """Every run we can still read. Unreadable lines are skipped, not fatal."""
    path = path or journal_file()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    runs: list[Run] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            runs.append(Run.from_dict(json.loads(line)))
        except (ValueError, KeyError, TypeError):
            continue
    return runs


def last(path: Path | None = None) -> Run | None:
    runs = read(path)
    return runs[-1] if runs else None


def _ago(days: int) -> str:
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    return f"{days} days ago"


SETUP_FIRST = (
    "  The setup on this computer is not finished yet: `ghti check` lists what is"
    " missing. A daily task started before that stops every morning on the"
    " configuration, so that is the thing to fix first, not the task."
)


def summary(
    run: Run | None,
    now: datetime | None = None,
    schedule_hint: bool = True,
    setup_ok: bool = True,
) -> str:
    """What to print under `ghti status`: the last run, in two lines at most.

    `setup_ok` is False when a required setting is missing. An empty or old
    diary then has a likelier cause than a scheduler that never fired, so
    saying "the daily task is not firing" would send the user hunting in the
    wrong place: a task that fires and dies on the configuration leaves the
    same silence behind, because it stops before the diary is written.
    """
    now = now or config.utcnow()
    if run is None:
        lines = ["No sync has run on this computer yet (or the diary was deleted)."]
        if not setup_ok:
            lines.append(SETUP_FIRST)
        elif schedule_hint:
            lines.append("  Run one with: ghti sync")
        return "\n".join(lines)

    days = (now.astimezone(UTC).date() - run.at.astimezone(UTC).date()).days
    when = run.at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    head = f"Last {run.command}: {when} ({_ago(days)})"
    if run.error:
        lines = [f"{head} FAILED: {run.error}"]
    else:
        lines = [f"{head}, {run.sent} day(s) sent."]
        if run.failed:
            lines.append("  Some metrics failed: " + ", ".join(run.failed) + ".")
        if run.skipped:
            lines.append("  Not authorized: " + ", ".join(run.skipped) + ".")
    if days >= STALE_AFTER.days:
        lines.append(f"  Nothing has run for {days} days.")
        lines.append(
            SETUP_FIRST
            if not setup_ok
            else "  If it should run on its own, the daily task is not firing: check it,"
            " or print it again with `ghti schedule`."
        )
    return "\n".join(lines)
