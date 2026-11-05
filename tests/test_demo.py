"""`ghti demo`: the example output, with no setup, no credentials and no network.

Every test runs with respx active (see conftest), so if the demo tried to reach
Google or intervals.icu these tests would fail.
"""

from __future__ import annotations

import json

from ghti import examples
from ghti.cli import main
from ghti.mapping import METRICS


def test_demo_runs_with_nothing_configured(capsys):
    assert main(["demo"]) == 0
    out = capsys.readouterr().out
    assert "Example data, invented" in out
    assert "Would send 3 day(s) to intervals.icu (2026-10-20 to 2026-10-22)." in out
    # the awkward cases, as promised in the note
    assert "sleepSecs=25200" in out  # the 7-hour night, the 25-minute nap left out
    assert "weight=72.9" in out  # the later weigh-in of the day wins
    assert "steps=10456" in out
    assert "2026-10-22: sleepSecs=23400" in out  # 0 steps that day: no steps column


def test_demo_json_shows_what_intervals_would_receive(capsys):
    assert main(["demo", "--json"]) == 0
    captured = capsys.readouterr()
    assert '"id": "2026-10-21"' in captured.out
    assert '"restingHR": 52' in captured.out
    # the words stay readable on a terminal, on the other stream
    assert "wellness-bulk" in captured.err
    assert "Example data, invented" in captured.err


def test_demo_json_stdout_is_json_and_nothing_else(capsys):
    """`ghti demo --json | jq` has to work: no prose mixed into stdout."""
    assert main(["demo", "--json"]) == 0
    records = json.loads(capsys.readouterr().out)
    assert [record["id"] for record in records] == ["2026-10-20", "2026-10-21", "2026-10-22"]
    assert records[1]["restingHR"] == 52


def test_every_metric_has_an_example():
    """A new or renamed metric must bring its example with it."""
    assert set(examples.BY_METRIC) == {metric.name for metric in METRICS}


def test_examples_cover_every_field_except_respiration():
    """The example week fills every wellness field the bridge can write."""
    fields = {reading.field for _, reading in examples.readings()}
    assert fields == {
        "sleepSecs",
        "restingHR",
        "hrv",
        "weight",
        "bodyFat",
        "spO2",
        "steps",
        "floorsClimbed",
        "vo2max",
    }
    assert examples.points(examples.RESPIRATION) == []  # on purpose: a metric with no data
