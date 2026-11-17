"""The operations workbook: generated from build_operations.py, and its queries follow the rules
the review asked for. Run: uvx pytest infra/tests/test_workbook.py"""

import json
import runpy
from pathlib import Path

HERE = Path(__file__).resolve().parents[1] / "workbooks"
BUILD = runpy.run_path(str(HERE / "build_operations.py"))


def items():
    return {i["content"].get("title"): i for i in BUILD["workbook"]()["items"] if i["type"] == 3}


def test_committed_workbook_is_current():
    assert json.loads((HERE / "operations.json").read_text()) == BUILD["workbook"]()


def test_month_tile_reads_the_whole_month_not_the_time_range():
    month = items()["This month against the budget"]["content"]
    assert "timeContextFromParameter" not in month and month["timeContext"]["durationMs"] >= 31 * 86400000


def test_speed_and_turn_counts_use_messages_only():
    for title in ("At a glance", "First text per backend, median and p95 (s)", "Speed per backend", "Users and turns per day"):
        assert 'kind == "message"' in items()[title]["content"]["query"], title
    assert 'kind == "message"' not in items()["Estimated cost per day (SEK)"]["content"]["query"]


def test_map_counts_users_by_pseudonym():
    query = items()["Where users connect from"]["content"]["query"]
    assert "voltrieve.user" in query and "user_Id" not in query
