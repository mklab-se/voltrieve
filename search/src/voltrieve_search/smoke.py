"""Smoke test: known questions must retrieve the expected document, planned as configured."""

from dataclasses import dataclass

from voltrieve_search.retrieve import KNOWLEDGE_SOURCE_NAME, RetrieveResult, retrieve

TOP_N = 5


@dataclass
class SmokeQuestion:
    question: str
    expect_title_contains: str
    expect_doc_type: str
    knowledge_base: str = "voltrieve-kb"
    knowledge_source: str = KNOWLEDGE_SOURCE_NAME


@dataclass
class SmokeOutcome:
    question: str
    passed: bool
    top_titles: list[str]
    planning_model: str | None = None


def _problems(result: RetrieveResult, expected_planner: str | None) -> list[str]:
    """What is wrong with the retrieve itself. The right document can come back all the same."""
    problems = [f"activity error: {a['error']}" for a in result.activity if a.get("error")]
    if result.partial:
        problems.append("partial result")
    if expected_planner and result.planning_model != expected_planner:
        problems.append(f"planned by {result.planning_model or 'no named model'}, expected {expected_planner}")
    return problems


def run_smoke(
    client, questions: list[SmokeQuestion], expected_planner: str | None = None
) -> list[SmokeOutcome]:
    outcomes = []
    for q in questions:
        result = retrieve(client, q.knowledge_base, q.question, knowledge_source=q.knowledge_source)
        refs = result.references[:TOP_N]
        sources = [r.source_data or {} for r in refs]
        found = any(
            q.expect_title_contains in str(s.get("title", "")) and s.get("doc_type") == q.expect_doc_type
            for s in sources
        )
        problems = _problems(result, expected_planner)
        outcome = SmokeOutcome(
            q.question, found and not problems, [str(s.get("title", "")) for s in sources], result.planning_model
        )
        print(
            f"{'PASS' if outcome.passed else 'FAIL'}  {q.question}  ->  {', '.join(outcome.top_titles)}"
            f"  (planned by {outcome.planning_model or 'no named model'})"
        )
        for problem in problems:
            print(f"  {problem}")
        outcomes.append(outcome)
    return outcomes
