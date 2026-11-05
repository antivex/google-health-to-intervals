"""The rule that decides how long a run waits when a server says "later"."""

from ghti.wait import MAX_WAIT_SECONDS, retry_wait, too_long


def test_a_short_retry_after_is_obeyed():
    assert retry_wait("12", fallback=5) == 12.0
    assert retry_wait(str(int(MAX_WAIT_SECONDS)), fallback=5) == MAX_WAIT_SECONDS


def test_a_long_retry_after_stops_the_run():
    assert retry_wait("3600", fallback=5) is None
    assert retry_wait(str(MAX_WAIT_SECONDS + 1), fallback=5) is None


def test_our_own_backoff_is_never_capped():
    """90 s is our number for the third try at intervals.icu: it must survive."""
    assert retry_wait(None, fallback=90) == 90
    assert retry_wait("not a number", fallback=90) == 90
    assert retry_wait("Wed, 05 Nov 2026 09:00:00 GMT", fallback=90) == 90


def test_odd_values_do_not_crash():
    assert retry_wait("  7.5  ", fallback=5) == 7.5  # isdigit() used to refuse this
    assert retry_wait("-5", fallback=5) == 0.0
    assert retry_wait("", fallback=5) == 5


def test_the_message_says_how_long_and_what_to_do():
    message = too_long("3600")
    assert "60 minutes" in message and "Run it again later" in message
    assert "nothing is lost" in message
    assert "45 seconds" in too_long("45.2")
