"""Writes operations.json, the Voltrieve operations workbook, from the queries below.

Run after editing: `python3 infra/workbooks/build_operations.py`. The workbook reads the
chat.turn spans the Web API writes (dependencies table) and the request location Application
Insights derives (requests table). __APPINSIGHTS_ID__ is replaced by Bicep at deployment, so the
same file works against any Application Insights resource with the same attributes.
"""

import json
from pathlib import Path

TURNS = """dependencies
| where name == "chat.turn"
| extend backend = tostring(customDimensions["voltrieve.backend"]),
    user = tostring(customDimensions["voltrieve.user"]),
    outcome = tostring(customDimensions["voltrieve.outcome"]),
    error_code = tostring(customDimensions["voltrieve.error_code"]),
    first_text = todouble(customDimensions["voltrieve.first_text_ms"]) / 1000.0,
    total = todouble(customDimensions["voltrieve.duration_ms"]) / 1000.0,
    cost = todouble(customDimensions["voltrieve.cost_sek"]),
    tools = tostring(customDimensions["voltrieve.tools"]),
    kbs = tostring(customDimensions["voltrieve.knowledge_bases"]),
    model_calls = toint(customDimensions["voltrieve.model_calls"]),
    input_tokens = toint(customDimensions["gen_ai.usage.input_tokens"]),
    output_tokens = toint(customDimensions["gen_ai.usage.output_tokens"]),
    cold_start = tostring(customDimensions["voltrieve.cold_start"]) == "True"
| where "{Backend}" == "all" or backend == "{Backend}"
"""

BUDGET_SEK = 990


def text(markdown: str) -> dict:
    return {"type": 1, "content": {"json": markdown}}


def query(title: str, kql: str, visualization: str, width: str = "100", size: int = 0, **extra) -> dict:
    content = {
        "version": "KqlItem/1.0",
        "query": kql,
        "size": size,
        "title": title,
        "timeContextFromParameter": "TimeRange",
        "queryType": 0,
        "resourceType": "microsoft.insights/components",
        "visualization": visualization,
        **extra,
    }
    return {"type": 3, "content": content, "customWidth": width}


def tiles(title: str, kql: str) -> dict:
    return query(title, kql, "tiles", tileSettings={
        "titleContent": {"columnMatch": "metric", "formatter": 1},
        "leftContent": {"columnMatch": "value", "formatter": 12, "formatOptions": {"palette": "auto"},
                        "numberFormat": {"unit": 0, "options": {"maximumFractionDigits": 2}}},
        "secondaryContent": {"columnMatch": "note", "formatter": 1},
        "showBorder": True,
    })


def parameters() -> dict:
    return {"type": 9, "content": {"version": "KqlParameterItem/1.0", "parameters": [
        {"id": "time", "version": "KqlParameterItem/1.0", "name": "TimeRange", "label": "Time range",
         "type": 4, "isRequired": True, "value": {"durationMs": 604800000},
         "typeSettings": {"selectableValues": [{"durationMs": ms} for ms in (
             3600000, 14400000, 86400000, 259200000, 604800000, 1209600000, 2592000000)]}},
        {"id": "backend", "version": "KqlParameterItem/1.0", "name": "Backend", "label": "Backend",
         "type": 2, "isRequired": True, "value": "all",
         "jsonData": json.dumps([{"value": v, "label": label} for v, label in (
             ("all", "All backends"), ("loop", "Own loop"), ("prompt", "Prompt agent"), ("hosted", "Hosted agent"))])},
    ]}}


def workbook() -> dict:
    items = [
        text("# Voltrieve operations\n"
             "Who uses the assistant, how fast it answers, which tools and knowledge bases it uses, "
             "and what it costs. For what is happening this second, open **Live Metrics** in Application "
             "Insights or the live dashboard in the web client. Costs are **estimates** from token counts "
             "and list prices, not the bill. Users are counted by pseudonym; prompts and answers are never "
             "stored."),
        parameters(),
        tiles("At a glance", TURNS + f"""| summarize users = dcount(user), turns = count(),
    first_text = percentile(first_text, 50), cost = sum(cost)
| project pack = pack_array(
    pack("metric", "Users", "value", todouble(users), "note", "distinct pseudonyms"),
    pack("metric", "Turns", "value", todouble(turns), "note", "requests to the Web API"),
    pack("metric", "First text, median (s)", "value", round(first_text, 2), "note", "seconds"),
    pack("metric", "Estimated cost (SEK)", "value", round(cost, 2), "note", "estimate, not the bill"))
| mv-expand pack
| project metric = tostring(pack.metric), value = todouble(pack.value), note = tostring(pack.note)"""),
        text("## Users"),
        query("Users and turns per day", TURNS + """| summarize users = dcount(user), turns = count() by bin(timestamp, 1d)
| order by timestamp asc""", "barchart", "50"),
        query("Where users connect from", """requests
| where name has "chat"
| where isnotempty(client_CountryOrRegion)
| summarize turns = count(), users = dcount(user_Id) by country = client_CountryOrRegion, city = client_City""",
              "map", "50", mapSettings={
                  "locInfo": "CountryRegion", "locInfoColumn": "country", "sizeSettings": "turns",
                  "sizeAggregation": "Sum", "legendMetric": "turns", "legendAggregation": "Sum",
                  "itemColorSettings": {"type": "heatmap", "colorAggregation": "Sum", "nodeColorField": "turns",
                                        "heatmapPalette": "greenRed"}}),
        text("## Speed"),
        query("First text per backend, median and p95 (s)", TURNS + """| where isnotnull(first_text)
| summarize p50 = percentile(first_text, 50), p95 = percentile(first_text, 95) by backend, bin(timestamp, 1h)
| order by timestamp asc""", "timechart", "60"),
        query("Speed per backend", TURNS + """| summarize turns = count(),
    first_text_p50 = round(percentile(first_text, 50), 2), first_text_p95 = round(percentile(first_text, 95), 2),
    total_p50 = round(percentile(total, 50), 2),
    over_10_s = round(100.0 * countif(first_text > 10 or (isnull(first_text) and total > 10)) / count(), 1)
    by backend
| order by backend asc""", "table", "40"),
        text("## Behavior"),
        query("Tools used", TURNS + """| where isnotempty(tools)
| mv-expand tool = split(tools, ",") to typeof(string)
| summarize calls = count() by tool
| order by calls desc""", "barchart", "33"),
        query("Most used knowledge bases", TURNS + """| where isnotempty(kbs)
| mv-expand kb = split(kbs, ",") to typeof(string)
| summarize searches = count() by kb
| order by searches desc""", "piechart", "33"),
        query("Outcomes", TURNS + """| summarize turns = count() by outcome
| order by turns desc""", "piechart", "33"),
        query("Model calls per turn and curated share", TURNS + """| summarize model_calls_per_turn = round(avg(model_calls), 2),
    curated_share = round(100.0 * countif(outcome == "curated") / count(), 1),
    client_tool_rounds = countif(outcome == "client_tool") by backend""", "table", "100"),
        text("## Cost\nEstimated from token counts and list prices in `api/app/insights.py`. The prompt agent "
             "does not report Search reasoning tokens, so its estimate is low. Fixed costs (Search, registry, "
             "containers) are in **Cost Management** for the resource group."),
        query("Estimated cost per day (SEK)", TURNS + """| summarize cost_sek = sum(cost) by backend, bin(timestamp, 1d)
| order by timestamp asc""", "barchart", "60"),
        tiles("This month against the budget", TURNS + f"""| where timestamp >= startofmonth(now())
| summarize cost = sum(cost), turns = count()
| project pack = pack_array(
    pack("metric", "Estimated token cost this month (SEK)", "value", round(cost, 2), "note", "estimate"),
    pack("metric", "Monthly budget (SEK)", "value", {BUDGET_SEK}.0, "note", "all Azure costs, see Cost Management"),
    pack("metric", "Cost per turn (öre)", "value", round(100.0 * cost / max_of(turns, 1), 2), "note", "estimate"))
| mv-expand pack
| project metric = tostring(pack.metric), value = todouble(pack.value), note = tostring(pack.note)"""),
        text("## Health"),
        query("Errors and cold starts per day", TURNS + """| summarize errors = countif(outcome == "error"), cold_starts = countif(cold_start)
    by bin(timestamp, 1d)
| order by timestamp asc""", "barchart", "50"),
        query("Errors by code", TURNS + """| where outcome == "error"
| summarize turns = count() by error_code, backend
| order by turns desc""", "table", "50"),
        text("## Privacy check\nEvery attribute name stored on a turn, with the longest value seen. Only ids, "
             "names, counts and timings belong here: no prompt, answer or user name. A long value is worth a look."),
        query("Attributes stored on chat.turn", """dependencies
| where name == "chat.turn"
| mv-expand kv = customDimensions
| extend key = tostring(bag_keys(kv)[0])
| extend value = tostring(kv[key])
| summarize longest_value = max(strlen(value)), example = take_any(iif(strlen(value) <= 40, value, "")) by key
| order by key asc""", "table", "100"),
    ]
    return {
        "version": "Notebook/1.0",
        "items": items,
        "fallbackResourceIds": ["__APPINSIGHTS_ID__"],
        "$schema": "https://github.com/Microsoft/Application-Insights-Workbooks/blob/master/schema/workbook.json",
    }


if __name__ == "__main__":
    out = Path(__file__).with_name("operations.json")
    out.write_text(json.dumps(workbook(), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out}")
