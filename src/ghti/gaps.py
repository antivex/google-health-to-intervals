"""Which days are really on intervals.icu, and which are not.

The diary (`ghti status`) knows what *this computer tried to send*. It does not
know what is there. Measured on 6 November 2026 with a backfill that died after
90 of 200 days and was then finished by hand up to 30 September: the diary kept
saying that 2026-07-05 to 2026-10-22 were missing, all 110 days, while 88 of
them were already on intervals.icu. The diary is a belief; only intervals.icu
knows.

So this asks intervals.icu itself, window by window, which days hold at least
one of the fields we write, and names the rest. Cost, measured the same day
against the client's own chunk of 100 days: 1 request for a month, 4 for a
year, against a limit of 5,000 a day. Checking is about a thousand times
cheaper than the limit, so there is no reason not to check.

What this cannot know: a day the watch never recorded looks exactly like a day
that was lost. Both are "intervals.icu has nothing". The caller says so out
loud instead of calling every empty day a fault.
"""

from __future__ import annotations

from datetime import date, timedelta

from ghti.intervals import CHUNK_SIZE, IntervalsClient
from ghti.mapping import FIELDS


def days_present(client: IntervalsClient, start: date, end: date) -> set[date]:
    """The days between start and end that hold at least one field we write.

    One request per CHUNK_SIZE days. `fields` makes intervals.icu drop null
    values, so a day that comes back at all has something of ours on it.
    """
    present: set[date] = set()
    for window_start, window_end in windows(start, end):
        found = client.read_back(window_start, window_end, set(FIELDS))
        for key in found:
            try:
                day = date.fromisoformat(key)
            except ValueError:
                continue  # intervals.icu keys wellness by ISO date; anything else is not a day
            if start <= day <= end:
                present.add(day)
    return present


def missing(client: IntervalsClient, start: date, end: date) -> list[tuple[date, date]]:
    """The days with nothing of ours on intervals.icu, as whole ranges."""
    present = days_present(client, start, end)
    empty = []
    day = start
    while day <= end:
        if day not in present:
            empty.append(day)
        day += timedelta(days=1)
    return as_ranges(empty)


def as_ranges(days: list[date]) -> list[tuple[date, date]]:
    """[1,2,3,7] -> [(1,3),(7,7)]. Consecutive days are one line, not forty."""
    ranges: list[tuple[date, date]] = []
    for day in sorted(days):
        if ranges and day == ranges[-1][1] + timedelta(days=1):
            ranges[-1] = (ranges[-1][0], day)
        else:
            ranges.append((day, day))
    return ranges


def requests_for(start: date, end: date) -> int:
    """How many intervals.icu requests a check over this range costs."""
    span = (end - start).days + 1
    return max(1, -(-span // CHUNK_SIZE))


def text(ranges: list[tuple[date, date]], start: date, end: date) -> str:
    """What `ghti gaps` prints."""
    span = (end - start).days + 1
    if not ranges:
        return (
            f"Every day from {start} to {end} ({span} day(s)) has something of ours on intervals.icu."
        )
    empty = sum((last - first).days + 1 for first, last in ranges)
    lines = [
        f"intervals.icu has nothing of ours on {empty} of the {span} day(s) from {start} to {end}:"
    ]
    for first, last in ranges:
        days = (last - first).days + 1
        when = str(first) if days == 1 else f"{first} to {last}"
        lines.append(f"  {when} ({days} day(s))   ghti backfill --from {first} --to {last}")
    lines.append(
        "  A day you did not wear the watch looks the same as a day that was lost:"
        " a backfill of it will simply find nothing to send."
    )
    return "\n".join(lines)


def windows(start: date, end: date) -> list[tuple[date, date]]:
    """The range cut into the chunks the client reads: no hole, no overlap.

    The day after `stop`, not `stop` itself: a window that restarts on its own
    last day asks for that day twice and, when the last window is a single day,
    never ends. That is what this function did when it was first written, and
    the test suite hung instead of failing.
    """
    out: list[tuple[date, date]] = []
    while start <= end:
        stop = min(end, start + timedelta(days=CHUNK_SIZE - 1))
        out.append((start, stop))
        start = stop + timedelta(days=1)
    return out
