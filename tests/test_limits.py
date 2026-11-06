"""A broken number from the watch must not reach intervals.icu.

Every limit used here is quoted from Google's own machine-readable discovery
document (revision 20261105,
https://health.googleapis.com/$discovery/rest?version=v4, read 6 November 2026),
which states the admissible range in the field description itself:

- Weight.weightGrams: "Must be in the range `[0, 1000000]`" (so 0-1000 kg)
- BodyFat.percentage, DailyOxygenSaturation.averagePercentage,
  DailyVO2Max.vo2Max: "Must be in the range `[0, 100]`"
- HeartRate.beatsPerMinute: "Must be in the range `[1, 300]`"

No medical judgement is added on top of those: a weigh-in of 0.5 kg is inside
Google's range and is still sent, because deciding that a human cannot weigh
0.5 kg is not something this bridge can source.
"""

from datetime import date

import pytest
from ghti.mapping import drop_impossible, extract_sleep, merge, to_wellness_records


def weight_point(grams):
    return {
        "weight": {
            "weightGrams": grams,
            "sampleTime": {"civilTime": {"date": {"year": 2026, "month": 10, "day": 21}}},
        }
    }


def days_with(field, value, day=date(2026, 10, 21)):
    return {day: {field: value}}


def kept(days):
    notes = drop_impossible(days)
    return days, notes


# --- what Google's own contract rules out ------------------------------------


@pytest.mark.parametrize(
    "field,value",
    [
        ("weight", 2000.0),  # 2,000 kg: outside [0, 1000000] grams
        ("weight", -70.0),
        ("bodyFat", 120.0),
        ("bodyFat", -3.0),
        ("spO2", 150.0),
        ("vo2max", 250.0),
        ("restingHR", 400),
        ("restingHR", 0),
    ],
)
def test_a_value_outside_googles_documented_range_is_not_sent(field, value):
    days, notes = kept(days_with(field, value))
    assert days == {date(2026, 10, 21): {}}, f"{field}={value} still reached intervals.icu"
    assert len(notes) == 1
    assert field in notes[0] and "2026-10-21" in notes[0]


@pytest.mark.parametrize(
    "field,value",
    [
        ("weight", 1000.0),  # exactly the upper bound: inclusive
        ("weight", 0.5),  # inside Google's range: no medical limit is invented
        ("bodyFat", 100.0),
        ("spO2", 100.0),
        ("vo2max", 100.0),
        ("restingHR", 1),
        ("restingHR", 300),
    ],
)
def test_a_value_inside_the_range_is_kept(field, value):
    days, notes = kept(days_with(field, value))
    assert days == {date(2026, 10, 21): {field: value}}
    assert notes == []


# --- what arithmetic rules out, whatever the field --------------------------


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("field", ["weight", "hrv", "steps", "sleepSecs", "respiration"])
def test_not_a_number_never_reaches_the_request_body(field, value):
    """`json.loads` accepts the NaN and Infinity tokens, `json.dumps` writes
    them back, and neither is valid JSON: one of them in a 100-day chunk makes
    intervals.icu refuse the whole chunk."""
    days, notes = kept(days_with(field, value))
    assert days == {date(2026, 10, 21): {}}
    assert len(notes) == 1


@pytest.mark.parametrize("field", ["hrv", "steps", "floorsClimbed", "respiration", "sleepSecs"])
def test_a_negative_count_is_not_sent(field):
    days, notes = kept(days_with(field, -5))
    assert days == {date(2026, 10, 21): {}}
    assert len(notes) == 1


def test_a_night_longer_than_a_day_is_not_sent():
    """2,000 minutes asleep is 33 hours: no calendar day holds it."""
    point = {
        "sleep": {
            "interval": {"civilEndTime": {"date": {"year": 2026, "month": 10, "day": 21}}},
            "summary": {"minutesAsleep": "2000"},
        }
    }
    days = merge([(_sleep_metric(), r) for r in extract_sleep(point)])
    notes = drop_impossible(days)
    assert days == {date(2026, 10, 21): {}}
    assert len(notes) == 1 and "sleepSecs" in notes[0]


def test_exactly_24_hours_asleep_is_kept():
    days = days_with("sleepSecs", 86400)
    notes = drop_impossible(days)
    assert days == {date(2026, 10, 21): {"sleepSecs": 86400}}
    assert notes == []


# --- one broken number must not cost the rest -------------------------------


def test_the_other_fields_of_the_same_day_still_go():
    days = {date(2026, 10, 21): {"weight": 2000.0, "restingHR": 48, "steps": 9000}}
    notes = drop_impossible(days)
    assert days == {date(2026, 10, 21): {"restingHR": 48, "steps": 9000}}
    assert len(notes) == 1 and "weight" in notes[0]


def test_a_broken_day_does_not_stop_the_other_days():
    days = {
        date(2026, 10, 20): {"weight": 71.4},
        date(2026, 10, 21): {"weight": float("nan")},
        date(2026, 10, 22): {"weight": 71.2},
    }
    notes = drop_impossible(days)
    assert days == {
        date(2026, 10, 20): {"weight": 71.4},
        date(2026, 10, 21): {},
        date(2026, 10, 22): {"weight": 71.2},
    }
    assert len(notes) == 1 and "2026-10-21" in notes[0]


def test_the_note_says_intervals_icu_keeps_what_it_had():
    days = days_with("weight", 2000.0)
    notes = drop_impossible(days)
    assert "not sent" in notes[0]
    assert "intervals.icu" in notes[0]


def test_a_day_left_empty_sends_no_record_at_all():
    """A record with only an "id" would overwrite nothing, but it still costs a
    request and reads as a day that was synced."""
    days = days_with("weight", float("nan"))
    drop_impossible(days)
    assert to_wellness_records(days) == []


def test_every_number_sent_is_json_safe():
    import json
    import math

    days = {
        date(2026, 10, 20): {"weight": 71.4, "restingHR": 48},
        date(2026, 10, 21): {"weight": float("nan"), "hrv": float("inf"), "steps": 9000},
    }
    drop_impossible(days)
    body = json.dumps(to_wellness_records(days))
    assert "NaN" not in body and "Infinity" not in body
    for record in json.loads(body):
        for key, value in record.items():
            if key != "id":
                assert math.isfinite(value)


def _sleep_metric():
    from ghti.mapping import METRICS

    return next(m for m in METRICS if m.name == "sleep")


# --- the whole command, not only the gate -----------------------------------


def test_a_broken_weigh_in_leaves_the_rest_of_the_day_alone(settings, mock_http, capsys):
    """One scale sending 2,000 kg: the day still goes, without that field, and
    the command says so instead of writing a point no chart can survive."""
    import httpx
    from conftest import DT, INTERVALS_PUT, mock_google, mock_refresh, put_bodies, store_token
    from ghti.cli import main

    store_token(settings)
    mock_refresh(mock_http)
    broken = {
        "dataPoints": [
            {
                "weight": {
                    "weightGrams": 2_000_000,
                    "sampleTime": {"civilTime": {"date": {"year": 2026, "month": 10, "day": 21}}},
                }
            }
        ]
    }
    mock_google(mock_http, {f"{DT}/weight/dataPoints": httpx.Response(200, json=broken)})
    put = mock_http.put(INTERVALS_PUT).mock(return_value=httpx.Response(200))

    assert main(["sync"]) == 0

    sent = put_bodies(put)[0]
    day = next(r for r in sent if r["id"] == "2026-10-21")
    assert "weight" not in day
    assert day["restingHR"] == 52 and day["sleepSecs"] == 25200
    out = capsys.readouterr().out
    assert "2026-10-21: weight" in out and "not sent" in out
