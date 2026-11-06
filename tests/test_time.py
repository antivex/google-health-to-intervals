"""The time test: days, zones and the two nights a year when the clock moves.

A buyer in America/New_York is the first one who sees a wrong day, because
"a night belongs to the day you wake up" has to survive the end of daylight
saving time, when 01:30 local happens twice.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import httpx
import pytest

import ghti.config as config
from conftest import DT, GOOGLE_ROUTES, INTERVALS_PUT, mock_google, mock_refresh, put_bodies, store_token
from ghti.cli import main
from ghti.mapping import _local_day

NEW_YORK = "America/New_York"

# United States, 2026: DST ends on Sunday 1 November at 02:00 (EDT -4h -> EST -5h)
# and starts on Sunday 8 March at 02:00 (EST -5h -> EDT -4h).
# Source: the zoneinfo database on this machine (zoneinfo.ZoneInfo), read 2026-11-06.


def test_the_zone_database_on_this_machine_has_the_two_2026_switches():
    """The dates the other tests are built on, measured instead of assumed."""
    from zoneinfo import ZoneInfo

    ny = ZoneInfo(NEW_YORK)
    before = datetime(2026, 11, 1, 5, 30, tzinfo=UTC).astimezone(ny)
    after = datetime(2026, 11, 1, 12, 30, tzinfo=UTC).astimezone(ny)
    assert before.utcoffset().total_seconds() == -4 * 3600  # EDT
    assert after.utcoffset().total_seconds() == -5 * 3600  # EST
    spring = datetime(2026, 3, 8, 10, 30, tzinfo=UTC).astimezone(ny)
    assert spring.utcoffset().total_seconds() == -4 * 3600  # EDT already


@pytest.mark.parametrize(
    ("end_time", "offset", "expected"),
    [
        # Woke at 01:30 EDT on 1 November, before the clock went back.
        ("2026-11-01T05:30:00Z", "-14400s", date(2026, 11, 1)),
        # The same wall clock, 01:30, lived a second time as EST: still 1 November.
        ("2026-11-01T06:30:00Z", "-18000s", date(2026, 11, 1)),
        # Woke at 07:30 EST, well after the switch.
        ("2026-11-01T12:30:00Z", "-18000s", date(2026, 11, 1)),
        # 23:50 EDT on 31 October is still October, not 1 November in UTC.
        ("2026-11-01T03:50:00Z", "-14400s", date(2026, 10, 31)),
        # Spring forward: asleep at 23:00 EST on 7 March, awake 06:30 EDT on the 8th.
        ("2026-03-08T10:30:00Z", "-14400s", date(2026, 3, 8)),
        # 00:30 EDT on 8 March, the hour 02:00-03:00 never existed that night.
        ("2026-03-08T05:30:00Z", "-18000s", date(2026, 3, 8)),
    ],
)
def test_the_wake_up_day_survives_both_clock_changes(end_time, offset, expected):
    assert _local_day(end_time, offset) == expected


def test_a_timestamp_without_a_zone_is_read_as_utc():
    """Google sends RFC 3339 with the zone, but if one ever arrives without it
    the answer must not depend on where the run happens: the same night would
    land on a different day on the owner's PC (UTC+1) and on GitHub Actions (UTC).
    04:30 UTC minus four hours is 00:30 on 1 November; read as Rome time it would
    be 23:30 on 31 October."""
    assert _local_day("2026-11-01T04:30:00", "-14400s") == date(2026, 11, 1)


def test_the_day_is_cut_in_the_users_zone_not_in_the_machines(monkeypatch, tmp_path):
    """21:30 in New York is already the next day in UTC. Without GHTI_TIMEZONE a
    run on GitHub Actions would ask Google for a day that has not started yet and
    skip the one that just ended - which is what `ghti check` warns about."""
    monkeypatch.setattr(config, "utcnow", lambda: datetime(2026, 11, 6, 2, 30, tzinfo=UTC))
    for key, value in (
        ("GOOGLE_CLIENT_ID", "id"),
        ("GOOGLE_CLIENT_SECRET", "secret"),
        ("INTERVALS_API_KEY", "key"),
        ("GHTI_TOKEN_FILE", str(tmp_path / "token.enc")),
    ):
        monkeypatch.setenv(key, value)

    monkeypatch.setenv("GHTI_TIMEZONE", NEW_YORK)
    assert config.load_settings().today() == date(2026, 11, 5)

    monkeypatch.delenv("GHTI_TIMEZONE")
    assert config.load_settings().today() == datetime(2026, 11, 6, 2, 30, tzinfo=UTC).astimezone().date()


def _night(name: str, end_time: str, offset: str, minutes: str) -> dict:
    """A sleep data point with no civilEndTime, so the zone math is exercised."""
    return {
        "dataPointName": f"users/me/dataTypes/sleep/dataPoints/{name}",
        "sleep": {
            "interval": {"endTime": end_time, "endUtcOffset": offset},
            "metadata": {"nap": False, "mainSleep": True},
            "summary": {"minutesAsleep": minutes},
        },
    }


@pytest.fixture
def new_york(tmp_path, monkeypatch):
    """A buyer in New York, syncing on Sunday 1 November 2026 at 14:00 local."""
    monkeypatch.setattr(config, "utcnow", lambda: datetime(2026, 11, 1, 19, 0, tzinfo=UTC))
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "123456-test.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret-XYZ")
    monkeypatch.setenv("INTERVALS_API_KEY", "test-intervals-key-ABC")
    monkeypatch.setenv("INTERVALS_ATHLETE_ID", "i42")
    monkeypatch.setenv("GHTI_TOKEN_FILE", str(tmp_path / "state" / "token.enc"))
    monkeypatch.setenv("GHTI_TIMEZONE", NEW_YORK)
    return config.load_settings()


def test_sync_over_the_night_the_clock_went_back(new_york, mock_http):
    """Three nights, two of them on either side of the switch, each on its own day."""
    store_token(new_york)
    mock_refresh(mock_http)
    nights = {
        "dataPoints": [
            _night("a", "2026-10-31T11:00:00Z", "-14400s", "400"),  # woke 07:00 EDT, 31 Oct
            _night("b", "2026-11-01T05:30:00Z", "-14400s", "390"),  # woke 01:30 EDT, 1 Nov
            _night("c", "2026-11-01T13:00:00Z", "-18000s", "60"),  # back to bed, 08:00 EST, 1 Nov
        ]
    }
    empty = httpx.Response(200, json={"dataPoints": [], "rollupDataPoints": []})
    overrides = {path: empty for _, path in GOOGLE_ROUTES if "sleep" not in path}
    overrides[f"{DT}/sleep/dataPoints:reconcile"] = httpx.Response(200, json=nights)
    google = mock_google(mock_http, overrides)
    put = mock_http.put(INTERVALS_PUT).mock(return_value=httpx.Response(200))

    assert main(["sync", "--days", "3"]) == 0

    # The window is cut on New York days: 30 October to 1 November included.
    sleep_filter = google[f"{DT}/sleep/dataPoints:reconcile"].calls[0].request.url.params["filter"]
    assert sleep_filter == (
        'sleep.interval.civil_end_time >= "2026-10-30" AND sleep.interval.civil_end_time < "2026-11-02"'
    )
    # Two sleep sessions that end on Sunday 1 November are added up (390 + 60 =
    # 450 minutes), as `combine="sum"` says, and go out as ONE record for that
    # day: the clock change must not split a day into two rows.
    assert put_bodies(put) == [
        [
            {"id": "2026-10-31", "sleepSecs": 24000},
            {"id": "2026-11-01", "sleepSecs": 27000},
        ]
    ]


def test_a_nap_after_the_clock_change_is_not_added_to_the_night(new_york, mock_http):
    store_token(new_york)
    mock_refresh(mock_http)
    nights = {
        "dataPoints": [
            _night("b", "2026-11-01T05:30:00Z", "-14400s", "390"),
            dict(
                _night("nap", "2026-11-01T18:00:00Z", "-18000s", "25"),
                sleep={
                    "interval": {"endTime": "2026-11-01T18:00:00Z", "endUtcOffset": "-18000s"},
                    "metadata": {"nap": True},
                    "summary": {"minutesAsleep": "25"},
                },
            ),
        ]
    }
    empty = httpx.Response(200, json={"dataPoints": [], "rollupDataPoints": []})
    overrides = {path: empty for _, path in GOOGLE_ROUTES if "sleep" not in path}
    overrides[f"{DT}/sleep/dataPoints:reconcile"] = httpx.Response(200, json=nights)
    mock_google(mock_http, overrides)
    put = mock_http.put(INTERVALS_PUT).mock(return_value=httpx.Response(200))

    assert main(["sync", "--days", "3"]) == 0
    assert put_bodies(put) == [[{"id": "2026-11-01", "sleepSecs": 23400}]]
