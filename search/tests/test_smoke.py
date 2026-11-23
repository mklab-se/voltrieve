import voltrieve_search.smoke as smoke
from voltrieve_search.retrieve import Reference, RetrieveResult
from voltrieve_search.smoke import SmokeQuestion, run_smoke


def ref(title, doc_type):
    return Reference(id="0", doc_key="k", reranker_score=3.0, source_data={"title": title, "doc_type": doc_type})


def test_smoke_passes_on_matching_reference(monkeypatch):
    monkeypatch.setattr(
        smoke,
        "retrieve",
        lambda c, kb, q, knowledge_source: RetrieveResult([], [ref("voltrieve-fjord-specifications.pdf", "spec")], [], False),
    )
    assert run_smoke(None, [SmokeQuestion("q", "fjord-specifications", "spec")])[0].passed


def test_smoke_fails_on_wrong_doc_type(monkeypatch):
    monkeypatch.setattr(
        smoke,
        "retrieve",
        lambda c, kb, q, knowledge_source: RetrieveResult([], [ref("voltrieve-fjord-brochure.docx", "brochure")], [], False),
    )
    assert not run_smoke(None, [SmokeQuestion("q", "fjord-specifications", "spec")])[0].passed


def test_cli_missing_env_var_exits_1_with_short_message(monkeypatch, capsys):
    from voltrieve_search.cli import main

    monkeypatch.delenv("SEARCH_ENDPOINT", raising=False)
    assert main(["retrieve", "q"]) == 1
    assert "SEARCH_ENDPOINT" in capsys.readouterr().err


def test_smoke_prints_activity_errors_when_partial(monkeypatch, capsys):
    monkeypatch.setattr(
        smoke,
        "retrieve",
        lambda c, kb, q, knowledge_source: RetrieveResult([], [], [{"type": "searchIndex", "error": {"message": "timeout"}}], True),
    )
    run_smoke(None, [SmokeQuestion("q", "x", "spec")])
    assert "timeout" in capsys.readouterr().out


def test_smoke_names_the_model_that_planned(monkeypatch, capsys):
    activity = [{"type": "modelQueryPlanning", "model": {"modelName": "gpt-6-luna"}}]
    monkeypatch.setattr(
        smoke,
        "retrieve",
        lambda c, kb, q, knowledge_source: RetrieveResult([], [ref("a-spec", "spec")], activity, False),
    )
    assert run_smoke(None, [SmokeQuestion("q", "a-spec", "spec")])[0].planning_model == "gpt-6-luna"
    assert "planned by gpt-6-luna" in capsys.readouterr().out


def planned(name="gpt-6-luna"):
    return {"type": "modelQueryPlanning", "model": {"modelName": name}}


def smoke_with(monkeypatch, activity, partial=False, **kwargs):
    """One question whose document is found, so only the rest of the answer can fail it."""
    result = RetrieveResult([], [ref("a-spec", "spec")], activity, partial)
    monkeypatch.setattr(smoke, "retrieve", lambda c, kb, q, knowledge_source: result)
    return run_smoke(None, [SmokeQuestion("q", "a-spec", "spec")], **kwargs)[0]


def test_smoke_fails_when_another_model_planned(monkeypatch, capsys):
    outcome = smoke_with(monkeypatch, [planned("gpt-5.4-mini")], expected_planner="gpt-6-luna")
    assert not outcome.passed
    out = capsys.readouterr().out
    assert out.startswith("FAIL") and "gpt-5.4-mini" in out and "expected gpt-6-luna" in out


def test_smoke_fails_when_no_model_planned(monkeypatch):
    assert not smoke_with(monkeypatch, [{"type": "searchIndex"}], expected_planner="gpt-6-luna").passed


def test_smoke_passes_when_the_expected_model_planned(monkeypatch):
    assert smoke_with(monkeypatch, [planned()], expected_planner="gpt-6-luna").passed


def test_smoke_fails_on_a_partial_result(monkeypatch):
    assert not smoke_with(monkeypatch, [planned()], partial=True).passed


def test_smoke_fails_on_an_activity_error_of_a_complete_result(monkeypatch, capsys):
    failed = planned() | {"error": {"message": "the planner was refused"}}
    assert not smoke_with(monkeypatch, [failed], expected_planner="gpt-6-luna").passed
    assert "the planner was refused" in capsys.readouterr().out


def test_a_null_error_is_not_an_error(monkeypatch):
    assert smoke_with(monkeypatch, [planned() | {"error": None}]).passed


def test_the_smoke_command_expects_the_planning_model_of_the_environment(monkeypatch):
    import voltrieve_search.cli as cli

    seen = []
    monkeypatch.setattr(cli, "make_client", lambda: None)
    monkeypatch.setattr(cli, "run_smoke", lambda c, q, expected_planner=None: seen.append(expected_planner) or [])
    monkeypatch.setenv("PLANNING_MODEL", "gpt-6-luna")
    assert cli.main(["smoke"]) == 0
    monkeypatch.delenv("PLANNING_MODEL")
    assert cli.main(["smoke"]) == 0
    assert seen == ["gpt-6-luna", None]


def test_the_hook_gives_the_smoke_test_the_planning_model():
    from pathlib import Path

    hook = (Path(__file__).resolve().parents[2] / "hooks" / "postup.sh").read_text()
    assert "export SEARCH_ENDPOINT PLANNING_MODEL" in hook


def test_smoke_questions_name_document_types_that_exist():
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    questions = json.loads((root / "search" / "smoke-questions.json").read_text(encoding="utf-8"))
    manifest = json.loads((root / "data" / "generated" / "manifest.json").read_text(encoding="utf-8"))
    docs = manifest["documents"] if isinstance(manifest, dict) else manifest
    types = {d["doc_type"] for d in docs}
    files = " ".join(Path(d["file"]).name for d in docs)
    for q in questions:
        assert q["expect_doc_type"] in types, q["question"]
        assert q["expect_title_contains"] in files, q["question"]
