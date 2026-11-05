"""How long we are willing to wait when a server says "try again later".

Both APIs can answer 429 (and intervals.icu also 502/503/504) with a
`Retry-After` header. Obeying that number blindly is a trap: Google documents
per-minute quotas but a project-level quota error can ask for an hour, and
intervals.icu sits behind a proxy that may ask for the same. With three retries
the command would sit there for up to three hours printing nothing - on a PC it
looks frozen, on GitHub Actions it burns the free minutes until the job timeout.

So a long wait asked by the server is refused instead of slept through: the run
stops with a message that says how long it asked for. Nothing is lost, because a
sync is idempotent - the next run copies the same days again.

Our own backoff (5/10/20 s for Google, 30/60/90 s for intervals.icu) is never
capped: those numbers are ours and already short.
"""

from __future__ import annotations

MAX_WAIT_SECONDS = 60.0  # the longest wait we accept from a server


def _seconds(header: str | None) -> float | None:
    """The header as seconds, or None when it is missing or not a number.

    `Retry-After` also has an HTTP-date form ("Wed, 05 Nov 2026 09:00:00 GMT").
    Neither API uses it, and guessing a date wrong would mean a wrong wait, so an
    unreadable header falls back to our own backoff.
    """
    if header is None:
        return None
    try:
        return float(str(header).strip())
    except ValueError:
        return None


def retry_wait(header: str | None, fallback: float) -> float | None:
    """Seconds to sleep before trying again, or None to stop instead of waiting.

    None means the server asked for more than MAX_WAIT_SECONDS; the caller then
    gives up on this run and says so, with `too_long()` for the words.
    """
    asked = _seconds(header)
    if asked is None:
        return max(fallback, 0.0)
    if asked > MAX_WAIT_SECONDS:
        return None
    return max(asked, 0.0)


def too_long(header: str | None) -> str:
    """Plain words for a wait we refuse, to add to the error shown."""
    asked = _seconds(header) or 0.0
    how_long = f"{round(asked / 60)} minutes" if asked >= 120 else f"{round(asked)} seconds"
    return (
        f" It asked to wait {how_long}, longer than the {round(MAX_WAIT_SECONDS)} seconds this command "
        "waits, so it stopped now instead of blocking. Run it again later: the same days are copied "
        "again, nothing is lost."
    )
