"""Read days from Google Health and write them to intervals.icu.

A sync is idempotent: the same days always produce the same records, and
intervals.icu simply overwrites those fields with the same values. Fields
with no data are not sent, so they never erase existing values.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import date, timedelta

import httpx

from ghti.config import Settings
from ghti.crypto import Keys
from ghti.errors import AuthRequired, GhtiError, GoogleHealthError, describe
from ghti.google_health import GoogleHealthClient
from ghti.intervals import IntervalsClient
from ghti.mapping import FIELDS, METRICS, confirm_written, drop_impossible, merge, to_wellness_records
from ghti.oauth import StoredToken, TokenStore, refresh_access_token

WINDOW_DAYS = 90  # long ranges are processed (and pushed) 90 days at a time


@dataclass
class SyncReport:
    start: date
    end: date
    days: dict[date, dict] = field(default_factory=dict)
    records_sent: int = 0
    records_confirmed: int | None = None  # None: not read back (dry run, --no-confirm, or the check failed)
    ok: set[str] = field(default_factory=set)
    failed: dict[str, str] = field(default_factory=dict)
    skipped: set[str] = field(default_factory=set)
    notes: list[str] = field(default_factory=list)
    token_updated: bool = False
    dry_run: bool = False

    def text(self, show_values: bool = False) -> str:
        verb = "Would send" if self.dry_run else "Sent"
        lines = [f"{verb} {self.records_sent} day(s) to intervals.icu ({self.start} to {self.end})."]
        if self.records_confirmed is not None:
            lines.append(f"  {self.records_confirmed} confirmed on intervals.icu of {self.records_sent} sent.")
        for day in sorted(self.days):
            values = self.days[day]
            shown = ", ".join(f"{k}={values[k]}" if show_values else k for k in FIELDS if k in values)
            lines.append(f"  {day}: {shown or '(no data)'}")
        lines += [f"  skipped {name}: permission not granted" for name in sorted(self.skipped)]
        lines += [f"  FAILED {name}: {error}" for name, error in sorted(self.failed.items())]
        lines += [f"  note: {note}" for note in self.notes]
        return "\n".join(lines)

    def markdown(self, show_values: bool = False) -> str:
        verb = "would be sent" if self.dry_run else "sent"
        out = [
            "## Google Health to intervals.icu",
            "",
            f"{self.records_sent} day(s) {verb}, from {self.start} to {self.end}.",
            "",
        ]
        if self.records_confirmed is not None:
            out += [f"{self.records_confirmed} confirmed on intervals.icu of {self.records_sent} sent.", ""]
        out += [
            "| Day | " + " | ".join(FIELDS) + " |",
            "|---|" + "---|" * len(FIELDS),
        ]
        for day in sorted(self.days):
            values = self.days[day]
            cells = [str(values[k]) if show_values and k in values else ("yes" if k in values else "") for k in FIELDS]
            out.append(f"| {day} | " + " | ".join(cells) + " |")
        if self.skipped:
            out += ["", "Not authorized (tick every box at the next authorization): " + ", ".join(sorted(self.skipped))]
        if self.failed:
            out += ["", "### Problems"] + [f"- **{name}**: {error}" for name, error in sorted(self.failed.items())]
        if self.notes:
            out += ["", "### Notes"] + [f"- {note}" for note in self.notes]
        return "\n".join(out) + "\n"


class SyncInterrupted(GhtiError):
    """A long range stopped part-way: the windows before the break are written.

    The message keeps the real cause first, then says how far the run got and
    the exact command that carries on from there. Nobody watches a scheduled
    run, so this text is also what `ghti status` shows the next morning.
    """

    def __init__(self, cause: Exception, done_through: date, end: date, written: int):
        self.cause = cause
        self.done_through = done_through  # last day we know is on intervals.icu
        self.resume_from = done_through + timedelta(days=1)
        self.exit_code = getattr(cause, "exit_code", 1)
        super().__init__(resume_text(cause, done_through, end, written))


def resume_text(cause: Exception, done_through: date, end: date, written: int) -> str:
    """The cause, then where the run got to, then how to carry on, in three lines.

    `written` can be 0 and the run still have got somewhere: a backfill of a
    year often starts before the user owned the watch, and those days are
    read, found empty and sent to nobody. Saying "0 day(s) are already on
    intervals.icu" would read like a run that did nothing at all, so those
    days are reported as read, not as written.
    """
    got_to = (
        f"  Stopped part-way: {written} day(s) up to {done_through} are already on intervals.icu."
        if written
        else f"  Stopped part-way: the days up to {done_through} were read and had no data to send."
    )
    return "\n".join(
        (
            describe(cause),
            got_to,
            f"  Carry on from there with: ghti backfill --from {done_through + timedelta(days=1)} --to {end}",
        )
    )


def windows(start: date, end: date, size: int = WINDOW_DAYS) -> Iterator[tuple[date, date]]:
    if size < 1:  # a belt: a size of 0 or less would never advance, and a run would hang forever
        raise ValueError(f"A window must be at least one day long, not {size}.")
    while start <= end:
        stop = min(end, start + timedelta(days=size - 1))
        yield start, stop
        start = stop + timedelta(days=1)


def open_google(settings: Settings, http: httpx.Client) -> tuple[GoogleHealthClient, StoredToken, bool]:
    """Load the stored token, get a fresh access token, save a rotated refresh token."""
    store = TokenStore(settings.token_file, Keys(settings.google()[1], settings.intervals_api_key))
    token = store.load()
    access_token, new_refresh = refresh_access_token(settings, token, http)
    updated = False
    if new_refresh and new_refresh != token.refresh_token:
        token = replace(token, refresh_token=new_refresh)
        updated = store.save(token)
    return GoogleHealthClient(http, access_token), token, updated


def collect(
    client: GoogleHealthClient, token: StoredToken, start: date, end: date, report: SyncReport
) -> dict[date, dict]:
    """Fetch every metric for start..end. A failing metric does not stop the others."""
    readings: list[tuple] = []
    for metric in METRICS:
        if token.scopes and metric.scope not in token.scopes:
            report.skipped.add(metric.name)
            continue
        try:
            points = client.fetch(metric, start, end)
        except GoogleHealthError as exc:
            report.failed[metric.name] = str(exc)
            continue
        report.ok.add(metric.name)
        for point in points:
            for reading in metric.extract(point):
                if start <= reading.day <= end:
                    readings.append((metric, reading))
    report.notes.extend(note for note in client.notes if note not in report.notes)
    days = merge(readings)
    # A number that cannot be a measurement is dropped here, at the one gate
    # every path goes through (sync, backfill, --dry-run and the CSV export),
    # so what the report shows is exactly what intervals.icu was sent.
    report.notes.extend(drop_impossible(days))
    return days


def _check(report: SyncReport) -> None:
    if report.failed and not report.ok:
        details = "\n".join(f"- {name}: {error}" for name, error in sorted(report.failed.items()))
        raise GhtiError("Nothing could be read from Google Health:\n" + details)


def _history_note(report: SyncReport) -> None:
    """Say out loud when Google had nothing for the days that were asked for.

    Google keeps a user's data from the day it was recorded and sets no limit on
    how far back an application may ask
    (https://developers.google.com/health/data-types, read 6 November 2026), so
    `backfill --from 2019-01-01` is a legal request that answers 200 with no data
    points: those days are simply empty, usually because the watch was not
    connected yet. Without this note the command printed `Sent 0 day(s)` and
    nothing else, which reads exactly like a broken setup.

    It stays quiet when something else already explains the emptiness (a
    permission that was not granted, a metric that failed), so the user is never
    sent looking at the wrong thing.
    """
    if report.failed or (report.skipped and not report.ok):
        return
    if not report.days:
        report.notes.append(
            f"Google Health had no data at all for {report.start} to {report.end}. "
            "Your history there starts the day the data was first recorded, so these days are empty "
            "on Google: nothing was sent and nothing is broken here. To see data, ask for a recent "
            "period (ghti sync)."
        )
        return
    first = min(report.days)
    if first > report.start:
        report.notes.append(
            f"Nothing before {first}: your history on Google Health starts there. "
            f"The days asked for earlier ({report.start} to {first - timedelta(days=1)}) are empty, "
            "so a backfill that starts before that day only costs requests."
        )


def run_sync(
    settings: Settings,
    start: date,
    end: date,
    http: httpx.Client,
    dry_run: bool = False,
    confirm: bool = True,
) -> SyncReport:
    client, token, updated = open_google(settings, http)
    intervals = IntervalsClient(http, settings.intervals_api_key, settings.intervals_athlete_id)
    report = SyncReport(start, end, token_updated=updated, dry_run=dry_run)
    done_through: date | None = None  # last day whose window went through in full
    confirmed = 0
    checking = confirm and not dry_run
    for window_start, window_end in windows(start, end):
        try:
            days = collect(client, token, window_start, window_end, report)
            records = to_wellness_records(days)
            if records and not dry_run:
                intervals.put_wellness(records)  # push each window, so progress is kept
        except (GhtiError, httpx.HTTPError) as error:
            if done_through is None or dry_run:
                raise  # nothing is written: re-running the same command is the right move
            raise _interrupted(error, done_through, end, report) from error
        if records and checking:
            kept = _confirm_window(intervals, records, window_start, window_end, report)
            if kept is None:
                checking = False  # the check itself failed: count nothing, promise nothing
            else:
                confirmed += kept
        report.days.update(days)
        report.records_sent += len(records)
        done_through = window_end
    if checking and report.records_sent:
        report.records_confirmed = confirmed
        if confirmed == 0:
            report.notes.append(
                f"intervals.icu answered 200 but kept none of the {report.records_sent} day(s) sent. "
                "Check INTERVALS_ATHLETE_ID (it must be your own athlete, or '0' for the key's owner)."
            )
    _check(report)
    _history_note(report)
    return report


def _confirm_window(
    intervals: IntervalsClient,
    records: list[dict],
    window_start: date,
    window_end: date,
    report: SyncReport,
) -> int | None:
    """Read the window back and say what is really on intervals.icu.

    Returns how many of the days sent are saved, or None when the check could
    not run. A failed check never fails the run: the days were sent, and
    turning a successful write into an error because the receipt could not be
    read would be worse than saying so. But it is said out loud, because
    "sent" on its own is what this whole check exists to stop promising.
    """
    fields = {name for record in records for name in record if name != "id"}
    try:
        found = intervals.read_back(window_start, window_end, fields)
    except (GhtiError, httpx.HTTPError) as error:
        report.notes.append(
            f"The {len(records)} day(s) of {window_start}..{window_end} were sent, but reading them back "
            f"did not work, so they are not verified: {describe(error)}"
        )
        return None
    problems = confirm_written(records, found)
    report.notes.extend(problems)
    missing = {problem.split(" ", 1)[0] for problem in problems if " is not on intervals.icu" in problem}
    return len(records) - len(missing)


def _interrupted(error: Exception, done_through: date, end: date, report: SyncReport) -> GhtiError:
    """Turn a break halfway into an error that says where to carry on from.

    An expired authorization keeps its own class, so the CLI still shows the
    authorization link instead of a bare error; it only gains the resume line.

    The three numbers of the break are also attached to the exception, not only
    written into its text: the diary needs them as fields (how many days really
    landed, the first day that did not, the end that was asked for), and parsing
    them back out of an English sentence would be a trap for the next change.
    """
    written = report.records_sent
    if isinstance(error, AuthRequired):
        out: GhtiError = AuthRequired(resume_text(error, done_through, end, written))
    else:
        out = SyncInterrupted(error, done_through, end, written)
    out.done_through = done_through
    out.resume_from = done_through + timedelta(days=1)
    out.written = written
    out.asked_end = end
    return out


def export_csv(settings: Settings, start: date, end: date, http: httpx.Client) -> tuple[str, SyncReport]:
    """Same data as a sync, as CSV text (columns use intervals.icu field names)."""
    client, token, updated = open_google(settings, http)
    report = SyncReport(start, end, token_updated=updated, dry_run=True)
    for window_start, window_end in windows(start, end):
        report.days.update(collect(client, token, window_start, window_end, report))
    _check(report)
    _history_note(report)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(("date", *FIELDS))
    for day in sorted(report.days):
        values = report.days[day]
        writer.writerow((day.isoformat(), *(values.get(k, "") for k in FIELDS)))
    return buffer.getvalue(), report
