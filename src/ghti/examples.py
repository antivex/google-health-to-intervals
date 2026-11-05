"""Example Google Health responses, used by `ghti demo` and by the tests.

These are NOT real data: they are invented bodies shaped like the public v4
reference (read on 2026-10-22), so that anybody can see what this bridge would
write to intervals.icu without setting anything up and without a Google
account. No health data of any person is stored in this repository.

The example week is 20-22 October 2026, in a UTC+2 timezone, and it contains on
purpose the cases that confuse a sync: a nap (ignored), two weigh-ins in the
same day (the later one wins), a night that starts before midnight (it belongs
to the day you wake up) and a day with 0 steps (treated as "no data", so it
never overwrites intervals.icu).
"""

from __future__ import annotations

from ghti.mapping import METRICS, Metric, Reading


def _date(day: int) -> dict:
    return {"year": 2026, "month": 10, "day": day}


# Shapes follow the v4 reference (DataPoint resource), read on 2026-10-22.
SLEEP = {
    "dataPoints": [
        {
            "dataPointName": "users/me/dataTypes/sleep/dataPoints/a",
            "sleep": {
                "interval": {
                    "startTime": "2026-10-20T21:10:00Z",
                    "startUtcOffset": "7200s",
                    "endTime": "2026-10-21T05:00:00Z",
                    "endUtcOffset": "7200s",
                    "civilEndTime": {"date": _date(21), "time": {"hours": 7}},
                },
                "type": "STAGES",
                "metadata": {"nap": False, "mainSleep": True},
                "summary": {"minutesAsleep": "420", "minutesAwake": "35"},
            },
        },
        {
            "dataPointName": "users/me/dataTypes/sleep/dataPoints/b",
            "sleep": {
                "interval": {"endTime": "2026-10-21T13:30:00Z", "endUtcOffset": "7200s"},
                "metadata": {"nap": True},
                "summary": {"minutesAsleep": "25"},
            },
        },
        {
            "dataPointName": "users/me/dataTypes/sleep/dataPoints/c",
            "sleep": {
                "interval": {
                    "endTime": "2026-10-22T04:30:00Z",
                    "endUtcOffset": "7200s",
                    "civilEndTime": {"date": _date(22)},
                },
                "metadata": {"nap": False},
                "summary": {"minutesAsleep": "390"},
            },
        },
    ]
}
RESTING_HR = {"dataPoints": [{"dailyRestingHeartRate": {"date": _date(21), "beatsPerMinute": "52"}}]}
HRV = {
    "dataPoints": [{"dailyHeartRateVariability": {"date": _date(21), "averageHeartRateVariabilityMilliseconds": 48.27}}]
}
WEIGHT = {
    "dataPoints": [
        {
            "weight": {
                "sampleTime": {"physicalTime": "2026-10-21T05:30:00Z", "utcOffset": "7200s"},
                "weightGrams": 72400,
            }
        },
        {
            "weight": {
                "sampleTime": {"physicalTime": "2026-10-21T18:00:00Z", "utcOffset": "7200s"},
                "weightGrams": 72900,
            }
        },
    ]
}
SPO2 = {
    "dataPoints": [
        {
            "dailyOxygenSaturation": {
                "date": _date(21),
                "averagePercentage": 96.4,
                "lowerBoundPercentage": 93,
                "upperBoundPercentage": 99,
            }
        }
    ]
}
VO2MAX = {"dataPoints": [{"dailyVo2Max": {"date": _date(20), "vo2Max": 47.3, "estimated": True}}]}
RESPIRATION = {"dataPoints": []}
STEPS = {
    "rollupDataPoints": [
        {"civilStartTime": {"date": _date(20)}, "civilEndTime": {"date": _date(21)}, "steps": {"countSum": "8123"}},
        {"civilStartTime": {"date": _date(21)}, "civilEndTime": {"date": _date(22)}, "steps": {"countSum": "10456"}},
        {"civilStartTime": {"date": _date(22)}, "civilEndTime": {"date": _date(23)}, "steps": {"countSum": "0"}},
    ]
}
# body fat has the same shape as weight (an observation with a sample time):
# two readings on the same day, so the later one must win.
BODY_FAT = {
    "dataPoints": [
        {
            "bodyFat": {
                "sampleTime": {"physicalTime": "2026-10-21T05:31:00Z", "utcOffset": "7200s"},
                "percentage": 18.4,
            }
        },
        {
            "bodyFat": {
                "sampleTime": {
                    "physicalTime": "2026-10-21T18:02:00Z",
                    "utcOffset": "7200s",
                    # civilTime is output only and always comes back with the
                    # reading: the day is taken from it, the ordering from
                    # physicalTime, which the spec marks required.
                    "civilTime": {"date": _date(21), "time": {"hours": 20, "minutes": 2}},
                },
                "percentage": 18.9,
            }
        },
    ]
}
# floors has the same shape as steps: a daily roll-up, with 0 meaning "no data".
FLOORS = {
    "rollupDataPoints": [
        {"civilStartTime": {"date": _date(20)}, "civilEndTime": {"date": _date(21)}, "floors": {"countSum": "12"}},
        {"civilStartTime": {"date": _date(21)}, "civilEndTime": {"date": _date(22)}, "floors": {"countSum": "7"}},
        {"civilStartTime": {"date": _date(22)}, "civilEndTime": {"date": _date(23)}, "floors": {"countSum": "0"}},
    ]
}

# One example response per metric, by metric name (see METRICS in mapping.py).
BY_METRIC: dict[str, dict] = {
    "sleep": SLEEP,
    "resting heart rate": RESTING_HR,
    "HRV": HRV,
    "weight": WEIGHT,
    "body fat": BODY_FAT,
    "SpO2": SPO2,
    "steps": STEPS,
    "floors": FLOORS,
    "VO2max": VO2MAX,
    "respiratory rate": RESPIRATION,
}


def points(body: dict) -> list[dict]:
    """The data points of a response, whichever list name it uses."""
    return list(body.get("dataPoints") or body.get("rollupDataPoints") or [])


def readings() -> list[tuple[Metric, Reading]]:
    """Run every extractor on the example responses, as a real sync would."""
    found: list[tuple[Metric, Reading]] = []
    for metric in METRICS:
        for point in points(BY_METRIC.get(metric.name, {})):
            found += [(metric, reading) for reading in metric.extract(point)]
    return found
