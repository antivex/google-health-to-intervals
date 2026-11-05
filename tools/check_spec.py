#!/usr/bin/env python3
"""Check this bridge's field names against the Google Health API's own spec.

Everything in src/ghti/mapping.py was written from Google's prose documentation,
without a real account to answer back. This script removes most of that doubt
without any account, credential or quota: it downloads the API's
machine-readable discovery document - the same file Google's own client
libraries are generated from - and asserts, one by one, that

  * every data type we put in a URL exists,
  * every JSON field our extractors read exists in the schema of that data type,
  * every filter path we send is one of the filter patterns the API documents,
  * the response keys we parse and the page-size caps we assume are the real ones
    (including the 90-point page and 90-day range of the daily roll-up).

Run it after any change to mapping.py, and whenever Google ships a new revision:

    uv run python tools/check_spec.py        # or: python tools/check_spec.py

Exit code 0 means every assertion held; 1 prints the mismatches. No network
write, no authentication: the discovery document is public.
"""

from __future__ import annotations

import json
import sys
import urllib.request

DISCOVERY = "https://health.googleapis.com/$discovery/rest?version=v4"

# (data type in the URL, DataPoint union key, schema name, fields our extractors read)
DATA_TYPES: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("sleep", "sleep", "Sleep", ("summary", "metadata", "interval")),
    ("daily-resting-heart-rate", "dailyRestingHeartRate", "DailyRestingHeartRate", ("beatsPerMinute", "date")),
    (
        "daily-heart-rate-variability",
        "dailyHeartRateVariability",
        "DailyHeartRateVariability",
        (
            "averageHeartRateVariabilityMilliseconds",
            "deepSleepRootMeanSquareOfSuccessiveDifferencesMilliseconds",
            "date",
        ),
    ),
    ("weight", "weight", "Weight", ("weightGrams", "sampleTime")),
    ("body-fat", "bodyFat", "BodyFat", ("percentage", "sampleTime")),
    ("daily-oxygen-saturation", "dailyOxygenSaturation", "DailyOxygenSaturation", ("averagePercentage", "date")),
    ("steps", "steps", "Steps", ()),  # read through the daily roll-up, see below
    ("floors", "floors", "Floors", ()),  # idem: an interval type, summed by the roll-up
    ("daily-vo2-max", "dailyVo2Max", "DailyVO2Max", ("vo2Max", "date")),
    ("daily-respiratory-rate", "dailyRespiratoryRate", "DailyRespiratoryRate", ("breathsPerMinute", "date")),
)

# Nested fields our extractors walk into, as "Schema.field[.field]".
NESTED: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("SleepSummary", ("minutesAsleep",)),
    ("SleepMetadata", ("nap",)),
    ("SessionTimeInterval", ("civilEndTime", "endTime", "endUtcOffset")),
    ("ObservationSampleTime", ("civilTime", "physicalTime", "utcOffset")),
    ("CivilDateTime", ("date",)),
    ("DailyRollupDataPoint", ("civilStartTime", "steps", "floors")),
    ("StepsRollupValue", ("countSum",)),
    ("FloorsRollupValue", ("countSum",)),
    ("CivilTimeInterval", ("start", "end")),
    ("DailyRollUpDataPointsRequest", ("range", "windowSizeDays", "pageSize", "pageToken")),
    ("DailyRollUpDataPointsResponse", ("rollupDataPoints", "nextPageToken")),
    ("ListDataPointsResponse", ("dataPoints", "nextPageToken")),
    ("ReconcileDataPointsResponse", ("dataPoints", "nextPageToken")),
)

# Filter paths we send (mapping.py: Metric.filter_field, snake_case).
FILTERS = (
    "sleep.interval.civil_end_time",
    "daily_resting_heart_rate.date",
    "daily_heart_rate_variability.date",
    "weight.sample_time.civil_time",
    "body_fat.sample_time.civil_time",
    "daily_oxygen_saturation.date",
    "daily_vo2_max.date",
    "daily_respiratory_rate.date",
)

# Methods we call: resource path in the discovery document -> URL suffix we build.
METHODS = (
    ("list", "/dataPoints"),
    ("reconcile", "/dataPoints:reconcile"),
    ("dailyRollUp", "/dataPoints:dailyRollUp"),
)

SCOPES = (
    "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
)


def fetch(url: str = DISCOVERY) -> dict:
    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 - fixed https URL
        return json.loads(response.read().decode("utf-8"))


def properties_of(spec: dict, schema: str) -> dict:
    return spec["schemas"].get(schema, {}).get("properties") or {}


def properties(spec: dict, schema: str) -> set[str]:
    return set((spec["schemas"].get(schema, {}).get("properties") or {}).keys())


def filter_patterns(spec: dict) -> str:
    methods = spec["resources"]["users"]["resources"]["dataTypes"]["resources"]["dataPoints"]["methods"]
    return methods["list"]["parameters"]["filter"]["description"]


def check(spec: dict) -> list[str]:
    bad: list[str] = []
    point = properties(spec, "DataPoint")
    filters = filter_patterns(spec)
    methods = spec["resources"]["users"]["resources"]["dataTypes"]["resources"]["dataPoints"]["methods"]

    for data_type, union_key, schema, fields in DATA_TYPES:
        if union_key not in point:
            bad.append(f"DataPoint has no union key {union_key!r} (data type {data_type})")
        if schema not in spec["schemas"]:
            bad.append(f"no schema {schema!r} (data type {data_type})")
            continue
        missing = sorted(set(fields) - properties(spec, schema))
        if missing:
            bad.append(f"{schema} has no field(s) {', '.join(missing)}")

    for schema, fields in NESTED:
        if schema not in spec["schemas"]:
            bad.append(f"no schema {schema!r}")
            continue
        missing = sorted(set(fields) - properties(spec, schema))
        if missing:
            bad.append(f"{schema} has no field(s) {', '.join(missing)}")

    for path in FILTERS:
        # The API documents either the exact path or its {placeholder} pattern.
        pattern = path.split(".", 1)[1]
        if f"`{path}`" not in filters and f".{pattern}`" not in filters:
            bad.append(f"the filter {path!r} is not among the documented filter patterns")

    for name, suffix in METHODS:
        if name not in methods:
            bad.append(f"the API no longer has the method {name!r} ({suffix})")

    sleep_page = "maximum page size for `exercise` and `sleep` is 25"
    if sleep_page not in methods["list"]["parameters"]["pageSize"]["description"]:
        bad.append("the cap of 25 data points per sleep page is no longer stated: check page_size in mapping.py")

    # dailyRollUp takes its page size and range in the request body, not in the
    # query string: both caps are stated in that schema.
    rollup_request = properties_of(spec, "DailyRollUpDataPointsRequest")
    if "The maximum page size is `90`" not in (rollup_request.get("pageSize", {}).get("description") or ""):
        bad.append("the cap of 90 data points per roll-up page is no longer stated: check ROLLUP_PAGE_SIZE")
    if "maximum range for all other data types is 90 days" not in (
        rollup_request.get("range", {}).get("description") or ""
    ):
        bad.append("the 90-day maximum range of dailyRollUp is no longer stated: check MAX_ROLLUP_DAYS")

    documented = set()
    for method in methods.values():
        documented.update(method.get("scopes") or [])
    missing_scopes = sorted(set(SCOPES) - documented)
    if missing_scopes:
        bad.append(f"scope(s) no longer accepted by any method: {', '.join(missing_scopes)}")

    return bad


def main() -> int:
    try:
        spec = fetch()
    except Exception as error:  # noqa: BLE001 - a network failure is not a mismatch
        print(f"the discovery document could not be read: {error}")
        return 2
    bad = check(spec)
    checked = len(DATA_TYPES) + len(NESTED) + len(FILTERS) + len(METHODS) + 4
    print(f"Google Health API {spec.get('version')}, revision {spec.get('revision')}: {checked} checks")
    if bad:
        for line in bad:
            print(f"  MISMATCH: {line}")
        return 1
    print("  every data type, field, filter, method, page cap and scope we use is in the spec")
    return 0


if __name__ == "__main__":
    sys.exit(main())
