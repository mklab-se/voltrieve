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


def test_the_budget_is_the_one_of_the_resource_group():
    assert BUILD["BUDGET_SEK"] == 2000
    assert '"value", 2000.0' in items()["This month against the budget"]["content"]["query"]


def test_speed_and_turn_counts_use_messages_only():
    for title in ("At a glance", "First text per backend, median and p95 (s)", "Speed per backend", "Users and turns per day"):
        assert 'request_kind == "message"' in items()[title]["content"]["query"], title
    assert 'request_kind == "message"' not in items()["Estimated cost per day (SEK)"]["content"]["query"]


def test_no_query_names_a_column_kind():
    """kind is a KQL keyword: a column named kind fails the whole query (BadArgumentError)."""
    import re

    for title, item in items().items():
        assert not re.search(r"(?<!join )(?<!request_)\bkind\s*(==|=)", item["content"]["query"]), title


def test_the_privacy_check_also_counts_what_foundry_traces_and_shows_no_text():
    query = items()["Rows written by Foundry's tracing"]["content"]["query"]
    assert '"responsesapi"' in query and "genAIContent" in query and "dependencies" in query
    assert "From the client" in query and "data:image" in query
    # Only counts, lengths, names and times may leave the query: the rows themselves hold the text.
    summary = query.split("| summarize")[1]
    assert "take_any" not in summary and "make_list" not in summary and "project" not in summary


def test_map_counts_users_by_pseudonym():
    query = items()["Where users connect from"]["content"]["query"]
    assert "voltrieve.user" in query and "user_Id" not in query


ASK_TITLE = "## What people ask"
ALLOWED = {"People", "Conversations", "Topic", "CarModel", "Client", "GroupName", "Week", "Date",
           "metric", "value", "note", "pack"}


def ask_items():
    all_items = BUILD["workbook"]()["items"]
    start = next(n for n, i in enumerate(all_items) if i["type"] == 1 and i["content"]["json"].startswith(ASK_TITLE))
    return all_items[start:]


def ask_queries():
    return {i["content"]["title"]: i["content"] for i in ask_items() if i["type"] == 3}


def test_the_other_items_still_use_application_insights():
    for title, item in items().items():
        if title not in ask_queries():
            assert item["content"]["resourceType"] == "microsoft.insights/components", title
            assert "crossComponentResources" not in item["content"], title


def test_what_people_ask_runs_on_the_workspace():
    queries = ask_queries()
    assert len(queries) == 7
    for title, content in queries.items():
        assert content["resourceType"] == "microsoft.operationalinsights/workspaces", title
        assert content["crossComponentResources"] == ["__WORKSPACE_ID__"], title
        assert "VoltrieveInsights_CL" in content["query"], title


def test_what_people_ask_names_no_column_kind_and_shows_no_time_text():
    import re

    for title, content in ask_queries().items():
        assert not re.search(r"(?<!join )\bkind\s*(==|=)", content["query"]), title
        assert "TimeGenerated" not in content["query"], title


def test_every_query_follows_the_source_parameter():
    for title, content in ask_queries().items():
        assert 'Source == "{Source}"' in content["query"], title


def test_totals_pick_the_latest_run_of_the_latest_window():
    for title, content in ask_queries().items():
        if title.startswith("Missing nights"):
            continue
        assert "max(Run)" in content["query"], title
        if not title.startswith("Weekly trend"):
            assert "max(WindowEnd)" in content["query"], title


def test_the_source_parameter_offers_real_and_made_up_only():
    params = [p for i in ask_items() if i["type"] == 9 for p in i["content"]["parameters"]]
    assert [p["name"] for p in params] == ["Source"]
    assert params[0]["value"] == "real"
    values = json.loads(params[0]["jsonData"])
    assert [v["value"] for v in values] == ["real", "seeded"]
    assert values[1]["label"] == "Made up"


def test_the_queries_show_only_allowed_columns():
    import re

    for title, content in ask_queries().items():
        if title.startswith("Weekly trend"):
            continue
        shown = re.findall(r"\| project ([^\n]+)", content["query"])
        assert shown, title
        for line in shown:
            if "(" in line:
                assert line.split("=")[0].strip() in ALLOWED, (title, line)
                continue
            for column in re.split(r",\s*", line):
                assert column.strip() in ALLOWED, (title, column)


def test_missing_nights_rule():
    query = ask_queries()
    title, content = next((t, c) for t, c in query.items() if t.startswith("Missing nights"))
    assert 'Cut == "run"' in content["query"]
    # before 04:00 UTC the newest night may still run, or its rows may not be in yet
    assert "startofday(now()) - 31d to startofday(now() - 4h) - 2d" in content["query"]
    assert "04:00 UTC" in title
    assert "min(WindowEnd)" in content["query"] and "leftanti" in content["query"]
    assert "day before yesterday" in title and "last 30" in title
    assert "| project Date = " in content["query"]


def test_a_group_is_shown_with_its_topic():
    """Two topics can hold a group with the same name."""
    assert "| project Topic, GroupName, Conversations, People" in ask_queries()["Groups of the latest window"]["query"]


def test_the_text_says_real_and_made_up_are_never_added():
    text = ask_items()[0]["content"]["json"]
    assert "never added" in text and "Real and made up" in text
    assert "five people" in text and "rounded to five" in text


def test_no_query_names_something_the_engine_refuses():
    import re

    def queries(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "query" and isinstance(value, str):
                    yield value
                else:
                    yield from queries(value)
        elif isinstance(node, list):
            for value in node:
                yield from queries(value)

    found = list(queries(items()))
    assert found
    for query in found:
        assert not re.search(r"\blatest\b", query), query
        assert not re.search(r"(?:\blet|\bextend|\bsummarize|,)\s*last\b", query), query
