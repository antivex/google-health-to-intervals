"""The third reader of a gap: the GitHub run page.

A buyer who installed this bridge with "Use this template" never opens a
terminal. The run page is all they see, and nobody watches a scheduled run:
they come back the morning after the nightly sync turned red.

What was measured here on 6 November 2026, before this file existed: the job
summary of a part-way break did name the `ghti backfill` line that ends the
gap, so the command was not stuck in the log. Two things were wrong anyway.

1. The heading was `## Error`, the same words a run that wrote **nothing**
   gets. Exit code 7 exists precisely so "90 of 200 days landed" is not read
   as "nothing landed" (tests/test_unfinished.py); the page said the opposite.
2. The only way out it offered was a terminal command. In the cloud the way to
   end the gap is Actions > Sync > Run workflow with the day in `from` - the
   input the workflow already has - and the page never mentioned it.
"""

from __future__ import annotations

import pytest

from ghti.sync import PARTIAL
from test_unfinished import RESUME_FROM, broke_halfway  # noqa: F401

SYNC_WORKFLOW = "https://github.com/someone/my-health-sync/actions/workflows/sync.yml"


@pytest.fixture
def summary(github, broke_halfway) -> str:  # noqa: F811
    """The job summary GitHub shows after a 200-day sync broke at day 90."""
    broke_halfway()
    return github.read_text(encoding="utf-8")


def test_the_page_does_not_call_a_part_way_break_an_error(summary):
    """The heading has to separate "part of it landed" from "nothing landed"."""
    heading = summary.splitlines()[0]
    assert heading.startswith("## Stopped part-way")
    assert "## Error" not in summary


def test_the_page_offers_the_cloud_route_with_the_resume_day(summary):
    """Actions > Sync > Run workflow, with the exact day to type into `from`."""
    assert SYNC_WORKFLOW in summary
    assert "Run workflow" in summary
    assert "**from**" in summary
    assert str(RESUME_FROM) in summary


def test_the_page_keeps_the_command_for_whoever_has_a_computer(summary):
    """The terminal line stays, and says the same day as the cloud route."""
    assert f"ghti backfill --from {RESUME_FROM}" in summary


def test_the_page_says_the_gap_keeps_status_red(summary):
    """Without this line the buyer thinks the next nightly run fixes it."""
    assert f"exits {PARTIAL}" in summary
    assert "Nothing fills this gap on its own" in summary


def test_the_cause_and_the_error_help_are_still_there(summary):
    """The new heading goes above the error, never instead of it."""
    assert 'intervals.icu error HTTP 400: {"error":"bad day"}' in summary
    assert "### What stopped it" in summary
    assert "### Stuck on this error?" in summary


def test_a_run_that_wrote_nothing_still_reads_as_an_error(settings, mock_http, github, capsys):
    """Only a part-way break gets the new heading: everything else is an error."""
    from conftest import mock_refresh, store_token
    from ghti.cli import main

    store_token(settings)
    mock_refresh(mock_http)
    from test_long_range import _mock_everything

    _mock_everything(mock_http)
    import httpx

    mock_http.put("https://intervals.icu/api/v1/athlete/i42/wellness-bulk").mock(
        return_value=httpx.Response(400, json={"error": "bad day"})
    )
    assert main(["sync", "--days", "3"]) == 1
    text = github.read_text(encoding="utf-8")
    assert text.startswith("## Error")
    assert "Stopped part-way" not in text
