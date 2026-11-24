"""The helper scripts under scripts/ that have logic worth a test: evidence files and the smoke test."""

import json
import sys
from pathlib import Path

import httpx
import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import smoke  # noqa: E402
import voltrieve_client  # noqa: E402
from voltrieve_client import evidence_text, write_evidence  # noqa: E402


def test_evidence_text_replaces_em_dashes_and_strips_absolute_paths():
    root = str(voltrieve_client.ROOT)
    text = f"A \u2014 B. Saved {root}/docs/evidence/x.json and /Users/someone/secret/file.txt, C:\\Users\\bob\\f.txt."
    cleaned = evidence_text(text)
    assert "\u2014" not in cleaned and "A - B." in cleaned
    assert "Saved docs/evidence/x.json" in cleaned
    assert "someone" not in cleaned and "bob" not in cleaned and "/Users" not in cleaned
    assert cleaned.count("<path>") == 2


def test_evidence_text_leaves_model_output_otherwise_verbatim():
    text = "G\u00f6teborg \u2013 2\u00a0100 kg \u30105:0\u2020file.pdf\u3011 **bold**"
    assert evidence_text(text) == text


def test_write_evidence_applies_it(tmp_path):
    path = write_evidence(tmp_path / "out" / "x.txt", "a \u2014 b\n")
    assert path.read_text() == "a - b\n"


def test_the_fact_checks_see_the_answer_a_client_sees():
    pytest.importorskip("yaml")
    import voltrieve_facts

    # A citation marker is no longer stripped: the backends filter it, so the checks do not.
    assert "\u3010" in voltrieve_facts._normal("2100 kg \u30105:0\u2020f.pdf\u3011")


def sse(*events) -> str:
    return "".join(f"event: {n}\ndata: {json.dumps(d)}\n\n" for n, d in events)


def fake_deployment(
    indexer_status="success", failing_backend=None, healthy=True, code="K7Q", rejected=None, car_event=None, used=True
):
    def handler(request: httpx.Request) -> httpx.Response:
        url = request.url
        if url.path == "/healthz":
            return httpx.Response(200 if healthy or url.host != "dealer" else 503, json={"status": "ok"})
        if url.path.endswith("/status"):
            assert request.headers["authorization"] == "Bearer search-token"
            return httpx.Response(200, json={"lastResult": {"status": indexer_status, "itemsFailed": 0}})
        assert request.headers["authorization"] == "Bearer user-token"
        if url.path == "/attachments":
            assert b'name="file"' in request.content and smoke.sign_png() in request.content
            answer = {"id": None, "status": "rejected", "code": rejected} if rejected else {"id": "a1", "status": "ready"}
            return httpx.Response(201, json=answer)
        assert url.path == "/chat"
        body = json.loads(request.content)
        if body["client_tools"] and car_event:
            return httpx.Response(200, text=sse(car_event))
        if request.headers["x-agent-backend"] == failing_backend:
            return httpx.Response(200, text=sse(("error", {"code": "agent_unavailable", "message": "x"})))
        photo = body.get("attachments") == ["a1"]
        return httpx.Response(200, text=sse(
            *([("trace", {"step": "attachment_used"})] if photo and used else []),
            ("delta", {"text": f"The sign says {code}." if photo else "2100 kg"}),
            ("done", {"conversation_id": "c", "turn_id": "t", "backend": "loop", "usage": {}})))

    return handler


def run(handler, backends=("prompt", "loop", "hosted"), **options):
    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport)
    original = voltrieve_client.httpx.Client
    # Api builds its own client: route it through the same mock transport.
    voltrieve_client.httpx.Client = lambda **kw: original(**{**kw, "transport": transport})
    try:
        return smoke.run_checks(
            "https://api", "https://dealer", "https://search", lambda: "search-token", lambda: "user-token",
            list(backends), http, **options,
        )
    finally:
        voltrieve_client.httpx.Client = original


def test_smoke_checks_health_indexer_and_the_turns_of_each_backend():
    results = run(fake_deployment())
    assert [name for name, _, _ in results] == [
        "api /healthz", "dealer /healthz", "search indexer",
        "turn on prompt", "car on prompt", "photo on prompt",
        "turn on loop", "photo on loop",
        "turn on hosted", "photo on hosted",
    ]
    assert all(ok for _, ok, _ in results), results


def test_smoke_can_leave_out_what_an_older_api_does_not_have():
    results = run(fake_deployment(rejected="unreadable"), basic=True)
    assert [name for name, _, _ in results] == [
        "api /healthz", "dealer /healthz", "search indexer", "turn on prompt", "turn on loop", "turn on hosted",
    ]
    assert all(ok for _, ok, _ in results), results


def test_smoke_declares_the_tools_of_the_prompt_agent_of_the_car():
    from app.backends.prompt import CAR_AGENT_TOOL_NAMES, PromptBackend
    from app.schemas import ChatRequest

    body = ChatRequest(message="x", client_tools=smoke.CAR_TOOLS)
    names = {t.name for t in body.client_tools}
    assert names == CAR_AGENT_TOOL_NAMES and len(body.client_tools) == 5
    assert PromptBackend._agent_for(names) == "car"


def test_smoke_draws_a_photo_that_the_api_accepts():
    from app.attachments import identify

    data = smoke.sign_png()
    [part] = identify(data).process(data, "")
    assert part.detail == "320x320" and len(data) < 10_000


def test_smoke_accepts_the_code_in_any_case_and_refuses_part_of_it():
    by_name = {n: ok for n, ok, _ in run(fake_deployment(code="the sign reads k7q"), backends=("loop",))}
    assert by_name["photo on loop"] is True
    by_name = {n: ok for n, ok, _ in run(fake_deployment(code="K7"), backends=("loop",))}
    assert by_name["photo on loop"] is False


@pytest.mark.parametrize("options,note", [
    ({"rejected": "too_blurry"}, "too_blurry"),
    ({"code": "P"}, "did the photo reach the model?"),
    ({"used": False}, "not used"),
])
def test_smoke_reports_a_photo_that_does_not_work(options, note):
    by_name = {n: (ok, text) for n, ok, text in run(fake_deployment(**options), backends=("loop",))}
    assert by_name["photo on loop"][0] is False and note in by_name["photo on loop"][1]
    assert by_name["turn on loop"][0] is True


def test_smoke_reports_an_api_without_attachments():
    def old_api(request):
        if request.url.path == "/attachments":
            return httpx.Response(404, json={"detail": "Not Found"})
        return fake_deployment()(request)

    by_name = {n: (ok, text) for n, ok, text in run(old_api, backends=("loop",))}
    assert by_name["photo on loop"][0] is False and "404" in by_name["photo on loop"][1]


def test_smoke_reports_a_car_turn_that_is_refused_or_needs_a_tool():
    refused = ("error", {"code": "unsupported_client_tool", "message": "x"})
    by_name = {n: (ok, text) for n, ok, text in run(fake_deployment(car_event=refused), backends=("prompt",))}
    assert by_name["car on prompt"] == (False, "error event: unsupported_client_tool")
    assert by_name["turn on prompt"][0] is True and by_name["photo on prompt"][0] is True

    def with_a_tool_call(request):
        if request.url.path == "/chat/tool-results":
            return httpx.Response(200, text=sse(("delta", {"text": "x"}), ("done", {"conversation_id": "c"})))
        called = ("client_tool_calls", {"turn_id": "t", "conversation_id": "c", "calls": [
            {"call_id": "1", "name": "honk", "arguments": "{}"}]})
        return fake_deployment(car_event=called)(request)

    by_name = {n: (ok, text) for n, ok, text in run(with_a_tool_call, backends=("prompt",))}
    assert by_name["car on prompt"] == (False, "called a client tool")


def test_smoke_reports_each_kind_of_failure():
    by_name = {n: ok for n, ok, _ in run(fake_deployment(indexer_status="transientFailure", failing_backend="hosted"))}
    assert by_name["search indexer"] is False
    assert by_name["turn on hosted"] is False
    assert by_name["turn on loop"] is True and by_name["api /healthz"] is True


def test_smoke_survives_an_unreachable_service():
    def down(request):
        raise httpx.ConnectError("refused", request=request)

    results = run(down, backends=("loop",))
    assert [ok for _, ok, _ in results] == [False] * 5


def _routing():
    import importlib.util

    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("routing_check", root / "tests" / "routing" / "check.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up by name
    spec.loader.exec_module(module)
    return module


class _Trace:
    done = None

    def __init__(self, *events):
        self.events = events

    def trace(self, step):
        return [d for s, d in self.events if s == step]


def test_routing_overlap_tells_parallel_from_sequential():
    routing = _routing()
    together = [{"started_ms": 100, "finished_ms": 900}, {"started_ms": 200, "finished_ms": 800}]
    in_turn = [{"started_ms": 100, "finished_ms": 500}, {"started_ms": 500, "finished_ms": 800}]
    assert routing.overlap(together) is True
    assert routing.overlap(in_turn) is False
    assert routing.overlap([{"started_ms": None, "finished_ms": 1}] * 2) is False


def test_routing_reads_mcp_calls_and_tool_calls_the_same_way():
    routing = _routing()
    mcp = routing.Run("prompt", "q", "both", 1, expected=["docs", "parts"])
    routing.read_trace(mcp, _Trace(
        ("model_called", {}),
        ("mcp_tool_called", {"server": "voltrieve-kb", "started_ms": 10, "finished_ms": 90}),
        ("mcp_tool_called", {"server": "voltrieve-parts-kb", "started_ms": 20, "finished_ms": 80}),
    ))
    loop = routing.Run("loop", "q", "both", 1, expected=["docs", "parts"])
    routing.read_trace(loop, _Trace(
        ("model_called", {}), ("model_called", {}),
        ("tools_parallel_started", {}),
        ("server_tool_called", {"tool": "search_voltrieve_docs", "iteration": 1}),
        ("server_tool_called", {"tool": "search_spare_parts", "iteration": 1}),
        ("tools_parallel_finished", {"one_by_one_ms": 900, "wall_ms": 500}),
    ))
    assert mcp.right and mcp.together and loop.right and loop.together
    assert loop.saved_ms == 400 and loop.steps == 2


def test_routing_counts_a_sequential_pair_as_not_together():
    routing = _routing()
    run = routing.Run("loop", "q", "both", 1, expected=["docs", "parts"])
    routing.read_trace(run, _Trace(
        ("server_tool_called", {"tool": "search_voltrieve_docs", "iteration": 1}),
        ("server_tool_called", {"tool": "search_spare_parts", "iteration": 2}),
    ))
    assert run.right and run.together is False


def test_routing_flags_vain_and_missed_sources():
    routing = _routing()
    run = routing.Run("loop", "q", "docs", 1, expected=["docs"], used=["parts"])
    assert not run.right and run.vain == ["parts"] and run.missed == ["docs"]


def test_routing_questions_resolve_against_the_parts_index():
    routing = _routing()
    questions = routing.yaml.safe_load(routing.QUESTIONS.read_text())["questions"]
    index = json.loads(routing.INDEX.read_text())
    for question in questions:
        routing.message(question, index)
        if question["group"] == "both":
            assert routing.expected_articles(question, index)


def _facts():
    pytest.importorskip("yaml")
    pytest.importorskip("pydantic")
    import voltrieve_facts

    return voltrieve_facts, voltrieve_facts.load_catalog()


def _sek(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def test_trim_price_check_wants_the_total_or_both_parts():
    facts, cat = _facts()
    fjord = cat.by_slug("fjord")
    heath = next(c for c in fjord.colors if c.key == "heath")
    base, color = cat.trim_price(fjord, "Plus", "AWD", "SE"), cat.color_price(heath, "SE")
    expect = {"type": "trim_price", "model": "fjord", "trim": "Plus", "drive": "AWD", "color": "heath", "market": "SE"}
    assert facts.check(expect, f"The Fjord Plus AWD in Heath costs {_sek(base + color)} SEK.")[0]
    assert facts.check(expect, f"{_sek(base)} SEK, plus {_sek(color)} SEK for the Heath paint.")[0]
    assert not facts.check(expect, f"It costs {_sek(base)} SEK.")[0]


def test_pack_check_names_exactly_the_trims_that_include_it():
    facts, cat = _facts()
    expect = {"type": "pack_trims", "model": "fjord", "pack": "Sound"}
    assert facts.check(expect, "The Sound pack is included on the Fjord Prime only.")[0]
    assert not facts.check(expect, "The Sound pack is included on the Fjord Plus.")[0]


def test_financing_check_wants_the_monthly_payment_and_the_rate():
    facts, cat = _facts()
    fjord = cat.by_slug("fjord")
    x = cat.financing_example(cat.trim_price(fjord, "Plus", "RWD", "SE"), "SE")
    expect = {"type": "financing", "model": "fjord", "market": "SE"}
    rate = f"{x.nominal_rate * 100:.2f}"
    assert facts.check(expect, f"{_sek(x.monthly)} SEK per month at a nominal rate of {rate} %.")[0]
    assert not facts.check(expect, f"{_sek(x.monthly)} SEK per month at 3.9 %.")[0]


def test_field_check_wants_every_named_value():
    facts, cat = _facts()
    d = cat.by_slug("fjord").dimensions
    expect = {"type": "fields", "model": "fjord", "fields": ["dimensions.boot_l", "dimensions.boot_folded_l"]}
    assert facts.check(expect, f"{d.boot_l} litres, {_sek(d.boot_folded_l)} litres folded")[0]
    assert not facts.check(expect, f"{d.boot_l} litres")[0]


def test_wheel_check_wants_each_wheel_size_and_name():
    facts, cat = _facts()
    wheels = cat.by_slug("fjord").wheels
    expect = {"type": "wheels", "model": "fjord"}
    assert facts.check(expect, " and ".join(f"{w.inches} inch {w.name}" for w in wheels))[0]
    assert not facts.check(expect, f"{wheels[0].inches} inch {wheels[0].name}")[0]


def test_trim_adds_check_wants_each_added_item_and_pack():
    facts, cat = _facts()
    plus = next(t for t in cat.by_slug("fjord").trims if t.name == "Plus")
    expect = {"type": "trim_adds", "model": "fjord", "trim": "Plus"}
    full = "Fjord Plus adds " + ", ".join(plus.adds + [f"the {p} pack" for p in plus.includes_packs])
    assert facts.check(expect, full)[0]
    assert not facts.check(expect, "Fjord Plus adds " + plus.adds[0])[0]


def test_sales_checks_accept_the_usual_wording():
    facts, _ = _facts()
    adds = {"type": "trim_adds", "model": "fjord", "trim": "Plus"}
    assert facts.check(adds, "Power tailgate with hands-free opening, keyless entry, a 360-degree camera "
                             "and the Winter pack")[0]
    pack = {"type": "pack_trims", "model": "fjord", "pack": "Sound"}
    assert facts.check(pack, "The Sound pack is included on Fjord Prime. It's optional on Fjord Pure and Plus.")[0]


def test_pack_check_fails_an_answer_that_includes_the_wrong_trim():
    facts, _ = _facts()
    pack = {"type": "pack_trims", "model": "fjord", "pack": "Sound"}
    assert not facts.check(pack, "The Sound pack is included on the Fjord Plus and Prime, and is not on the Pure.")[0]
    assert facts.check(pack, "It is standard on the Fjord Prime, and not available as standard on Pure or Plus.")[0]


def test_routing_needs_every_listed_tool():
    routing = _routing()
    run = routing.Run("loop", "q", "parts", 1, expected=["parts"], expected_tools=["check_part_stock"])
    routing.read_trace(run, _Trace(("server_tool_called", {"tool": "search_spare_parts", "iteration": 1})))
    assert not run.right
    routing.read_trace(run, _Trace(
        ("server_tool_called", {"tool": "search_spare_parts", "iteration": 1}),
        ("server_tool_called", {"tool": "check_part_stock", "iteration": 2}),
    ))
    assert run.right and run.used == ["parts"] and run.tools == ["check_part_stock", "search_spare_parts"]


def _parts(facts, cat, model: str, key: str) -> list[dict]:
    name = cat.by_slug(model).name
    return [p for p in facts.load_parts_index()["parts"]
            if p["key"] == key and any(f == name or f.startswith(name + " ") for f in p["fits"])]


def test_part_stock_check_names_the_closest_dealer_with_stock():
    facts, cat = _facts()
    stock = facts.load_parts_stock()
    expect = {"type": "part_stock", "model": "spark", "key": "door_mirror_right", "city": "Stockholm"}
    article = _parts(facts, cat, "spark", "door_mirror_right")[0]["article"]
    assert stock["stock"][article]["Stockholm"] == 0
    km = stock["distances_km"]["Stockholm"]["Uppsala"]
    n = stock["stock"][article]["Uppsala"]
    assert facts.check(expect, f"None in Stockholm. Uppsala, {km} km from Stockholm, has {n}.")[0]
    assert not facts.check(expect, f"Göteborg has {stock['stock'][article]['Göteborg']}.")[0]


def test_part_stock_check_for_a_part_no_dealer_has():
    facts, _ = _facts()
    expect = {"type": "part_stock", "pick": "out_everywhere", "city": "Malmö"}
    values = facts.ask_values(expect)
    assert facts.load_parts_stock()["stock"][values["article"]] == dict.fromkeys(facts.load_parts_stock()["cities"], 0)
    assert facts.check(expect, "No Voltrieve dealer has that part in stock right now.")[0]
    assert not facts.check(expect, "Malmö has 2 in stock.")[0]


def test_article_and_both_sides_checks():
    facts, cat = _facts()
    wiper = _parts(facts, cat, "fjord", "wiper_blade_front")[0]["article"]
    assert facts.check({"type": "article", "model": "fjord", "key": "wiper_blade_front"}, f"Use {wiper}.")[0]
    assert not facts.check({"type": "article", "model": "fjord", "key": "wiper_blade_front"}, "Use VP-99999.")[0]
    left = _parts(facts, cat, "spark", "door_mirror_left")[0]["article"]
    right = _parts(facts, cat, "spark", "door_mirror_right")[0]["article"]
    both = {"type": "both_sides", "model": "spark", "key": "door_mirror"}
    assert facts.check(both, f"Left {left}, right {right}.")[0]
    assert not facts.check(both, f"Right {right}.")[0]


def test_tyre_check_follows_the_trim_wheel():
    facts, cat = _facts()
    fjord = cat.by_slug("fjord")
    plus = next(t for t in fjord.trims if t.name == "Plus")
    size = next(w.tyre_size for w in fjord.wheels if w.key == plus.wheel)
    tyre = next(p["article"] for p in _parts(facts, cat, "fjord", "tyre_winter_studless") if plus.wheel in p["wheels"])
    expect = {"type": "tyre", "model": "fjord", "season": "winter", "trim": "Plus"}
    assert facts.check(expect, f"The Plus has {size} tyres: studless {tyre}.")[0]
    other = next(w.tyre_size for w in fjord.wheels if w.key != plus.wheel)
    assert not facts.check(expect, f"Use {other}.")[0]
    sizes = {"type": "tyre", "model": "fjord", "season": "winter"}
    assert facts.check(sizes, f"Pure {other}, Plus and Prime {size}.")[0]
    assert not facts.check(sizes, f"{size}.")[0]


def test_no_engine_oil_check():
    facts, cat = _facts()
    oil = _parts(facts, cat, "ragn", "reduction_oil")[0]["article"]
    expect = {"type": "no_engine_oil", "model": "ragn"}
    assert facts.check(expect, f"The Ragn does not use engine oil. Its only oil is reduction gear oil, {oil}.")[0]
    assert not facts.check(expect, "Use engine oil VP-12345.")[0]


def _jwt(seconds_left: int) -> str:
    import base64
    import time

    body = base64.urlsafe_b64encode(json.dumps({"exp": int(time.time()) + seconds_left}).encode()).decode().rstrip("=")
    return f"e30.{body}.sig"


def test_cached_token_is_refetched_before_it_expires():
    tokens = iter([_jwt(120), _jwt(3600), _jwt(3600)])
    get = voltrieve_client.cached_token(lambda: next(tokens))
    first = get()
    assert get() != first  # two minutes left is too little for a long run
    second = get()
    assert get() == second  # an hour left is kept


# scripts/deploy-web.sh, run against fake az, azd and docker programs on PATH.

import os  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402

DEPLOY_WEB = SCRIPTS / "deploy-web.sh"
SITES = "stapp-voltrieve-dev\tweb\nstapp-voltrieve-car-dev\tcar\nstapp-voltrieve-dashboard-dev\tdashboard\n"


def _deploy(tmp_path, *args, sites=SITES, dists=("web", "car", "dashboard")):
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(DEPLOY_WEB, root / "scripts" / "deploy-web.sh")
    for service in dists:
        (root / "clients" / service / "dist").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    (tmp_path / "sites.tsv").write_text(sites)
    fakes = {
        "azd": 'echo "azd $*" >> "$FAKE_LOG"\necho AZURE_RESOURCE_GROUP=rg-test\n',
        "az": (
            'echo "az $*" >> "$FAKE_LOG"\n'
            'case "$*" in\n'
            '  "staticwebapp list"*) cat "$FAKE_SITES" ;;\n'
            '  "staticwebapp secrets list"*) echo made-up-token ;;\n'
            '  *) echo "unexpected az call" >&2; exit 9 ;;\n'
            'esac\n'
        ),
        "docker": 'echo "docker $*" >> "$FAKE_LOG"\n',
    }
    for name, body in fakes.items():
        path = bin_dir / name
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_LOG": str(log),
           "FAKE_SITES": str(tmp_path / "sites.tsv")}
    result = subprocess.run(["sh", str(root / "scripts" / "deploy-web.sh"), *args],
                            capture_output=True, text=True, env=env)
    return result, (log.read_text() if log.exists() else "")


def test_deploy_web_defaults_to_the_web_site(tmp_path):
    result, calls = _deploy(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "secrets list -n stapp-voltrieve-dev " in calls
    assert "clients/web/dist:/app/dist:ro" in calls


@pytest.mark.parametrize("service,site", [("car", "stapp-voltrieve-car-dev"), ("dashboard", "stapp-voltrieve-dashboard-dev")])
def test_deploy_web_picks_the_site_by_its_tag_not_by_position(tmp_path, service, site):
    # The wanted site is listed last and first in turn: position must not matter.
    for n, sites in enumerate((SITES, "".join(reversed(SITES.splitlines(keepends=True))))):
        result, calls = _deploy(tmp_path / str(n), service, sites=sites)
        assert result.returncode == 0, result.stderr
        assert f"secrets list -n {site} " in calls
        assert f"clients/{service}/dist:/app/dist:ro" in calls
        assert "made-up-token" not in calls


def test_deploy_web_refuses_an_unknown_service(tmp_path):
    result, calls = _deploy(tmp_path, "cli")
    assert result.returncode == 1
    assert "web, car or dashboard" in result.stderr
    assert calls == ""


def test_deploy_web_fails_when_no_site_has_the_tag(tmp_path):
    result, calls = _deploy(tmp_path, "car", sites="stapp-voltrieve-dev\tweb\n")
    assert result.returncode == 1
    assert "azd-service-name" in result.stderr and "car" in result.stderr
    assert "docker" not in calls and "secrets" not in calls


def test_deploy_web_fails_when_two_sites_share_the_tag(tmp_path):
    result, calls = _deploy(tmp_path, "car", sites=SITES + "stapp-other\tcar\n")
    assert result.returncode == 1
    assert "more than one" in result.stderr
    assert "docker" not in calls


def test_deploy_web_fails_when_the_build_is_missing(tmp_path):
    result, calls = _deploy(tmp_path, "dashboard", dists=("web", "car"))
    assert result.returncode == 1
    assert "clients/dashboard/dist" in result.stderr
    assert calls == ""


def test_deploy_agents_ends_by_naming_the_full_smoke_check():
    last = (SCRIPTS / "deploy-agents.sh").read_text().strip().splitlines()[-1]
    assert last.startswith("echo ") and "uv run scripts/smoke.py" in last and "--basic" not in last


@pytest.mark.parametrize(("label", "models"), [
    ("final-gpt-6.1-terra", ("gpt-6.1-terra", "gpt-5.4-mini")),
    ("planner-gpt-5.4-nano", ("gpt-5.4-mini", "gpt-5.4-nano")),
    ("novluna-gpt-6.1-terra+gpt-6-luna", ("gpt-6.1-terra", "gpt-6-luna")),
    ("x-loop-gpt-6.1-terra+gpt-6-luna", ("gpt-6.1-terra", "gpt-6-luna")),
    ("arch-gpt-6.1-terra", ("gpt-6.1-terra", "gpt-5.4-mini")),
    ("arch-gpt-6.1-terra+gpt-6-luna", ("gpt-6.1-terra", "gpt-6-luna")),
])
def test_model_costs_reads_both_models_from_the_label(label, models):
    import model_costs

    assert model_costs.models_of(label) == models


def test_model_costs_prices_an_architecture_run_by_its_planner(tmp_path, monkeypatch):
    import model_costs

    run = {"backend": "loop", "group": "none", "error": "", "seconds": 1.0, "first_text_seconds": 1.0,
           "used": [], "expected": [], "articles_found": 0, "articles_expected": 0, "searches": 1,
           "input_tokens": 0, "output_tokens": 0, "planning_input_tokens": 1_000_000,
           "planning_output_tokens": 0, "search_reasoning_tokens": 0}
    (tmp_path / "models").mkdir()
    for planner in ("gpt-6-luna", "gpt-5.4-mini"):
        (tmp_path / "models" / f"arch-gpt-6.1-terra+{planner}.json").write_text(json.dumps([run]))
    monkeypatch.setattr(model_costs, "EVIDENCE", tmp_path)
    assert model_costs.main() == 0
    rows = [line.split() for line in (tmp_path / "model-comparison.txt").read_text().splitlines()
            if line.startswith("loop")]
    assert {row[1]: row[-1] for row in rows} == {
        "gpt-6.1-terra+gpt-6-luna": "100.00", "gpt-6.1-terra+gpt-5.4-mini": "750.00"
    }


def test_every_tool_that_calls_the_deployed_chat_says_it_is_a_test():
    """Our own scripts and checks must send X-Voltrieve-Test, directly or through Api, so that
    their runs are never counted as use. Test files run in process and are not covered."""
    root = SCRIPTS.parent
    files = [*(root / "scripts").rglob("*.py"), *(root / "tests/routing").rglob("*.py"),
             *(root / "tests/grounding").rglob("*.py"), *(root / "tests/photos").rglob("*.py")]
    missing = []
    for path in files:
        if ".venv" in path.parts or "__pycache__" in path.parts:
            continue
        if path.name.startswith("test_") or path.name == "conftest.py":
            continue
        text = path.read_text()
        if '"/chat' in text and "Api(" not in text and "X-Voltrieve-Test" not in text:
            missing.append(str(path.relative_to(root)))
    assert missing == []


# --- tracing_check: marks a turn, then looks for the mark in every table of the workspace ---

import subprocess  # noqa: E402

import tracing_check  # noqa: E402


class FakeApi:
    sent: list[dict] = []

    def __init__(self, base_url, token, backend, timeout=180.0):
        self.backend = backend

    def turn(self, message, **kwargs):
        FakeApi.sent.append({"backend": self.backend, "message": message, **kwargs})
        return voltrieve_client.Turn(
            events=[("delta", {"text": "SECRET ANSWER TEXT"}), ("done", {"conversation_id": "c1"})]
        )

    def close(self):
        pass


def test_a_marker_is_letters_and_digits_only():
    marker = tracing_check.new_marker()
    assert marker.startswith("vtmark") and len(marker) == 18
    assert tracing_check.valid_marker(marker)
    for bad in ("", 'x" | project y', "a b", "a-b", "a\nb", "x'y", "ä"):
        assert not tracing_check.valid_marker(bad)


def test_the_search_query_holds_counts_only():
    query = tracing_check.search_query("vtmarkabc123", 4)
    assert '"*vtmarkabc123*"' in query
    assert "summarize" in query and "count()" in query and "$table" in query
    for word in ("project", "take", "limit", "top "):
        assert word not in query


def test_a_bad_marker_is_refused_before_any_subprocess(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a))
    assert tracing_check.main(["--search", 'x" | project y']) == 2
    assert calls == []


def test_mark_puts_the_marker_in_the_message_and_the_client_line(monkeypatch, capsys):
    FakeApi.sent = []
    monkeypatch.setattr(tracing_check, "Api", FakeApi)
    monkeypatch.setattr(tracing_check, "api_url", lambda: "http://api")
    monkeypatch.setattr(tracing_check, "api_token", lambda: "t")
    assert tracing_check.main(["--mark"]) == 0
    out = capsys.readouterr().out
    assert [s["backend"] for s in FakeApi.sent] == ["loop", "prompt", "hosted"]
    markers = set()
    for sent in FakeApi.sent:
        marker = next(w for w in sent["message"].split() if w.startswith("vtmark")).strip(".,?")
        markers.add(marker)
        trim = sent["context"]["car"]["trim"]
        assert marker in trim and len(trim) <= 40
        assert sent["context"]["car"]["model"] == "Voltrieve Spark"
        assert marker in out
        assert "correlation_id" not in sent or marker not in str(sent["correlation_id"])
    assert len(markers) == 3
    assert "SECRET ANSWER TEXT" not in out
    assert out.count("done") == 3


def test_mark_prints_the_error_code_not_the_answer(monkeypatch, capsys):
    class Failing(FakeApi):
        def turn(self, message, **kwargs):
            return voltrieve_client.Turn(events=[("delta", {"text": "SECRET"}), ("error", {"code": "boom"})])

    monkeypatch.setattr(tracing_check, "Api", Failing)
    monkeypatch.setattr(tracing_check, "api_url", lambda: "http://api")
    monkeypatch.setattr(tracing_check, "api_token", lambda: "t")
    tracing_check.main(["--mark"])
    out = capsys.readouterr().out
    assert "boom" in out and "SECRET" not in out


def fake_az(tables_by_marker):
    def run(cmd, **kwargs):
        if "--analytics-query" in cmd:
            query = cmd[cmd.index("--analytics-query") + 1]
            marker = next(m for m in tables_by_marker if m in query)
            rows = json.dumps([{"Table": t, "n": n} for t, n in tables_by_marker[marker].items()])
            return subprocess.CompletedProcess(cmd, 0, rows, "")
        return subprocess.CompletedProcess(cmd, 0, '["workspace-guid"]', "")

    return run


def test_search_exit_code_is_zero_when_nothing_is_found(monkeypatch, capsys):
    monkeypatch.setenv("AZURE_RESOURCE_GROUP", "rg")
    monkeypatch.setattr(subprocess, "run", fake_az({"vtmarkaaa": {}}))
    assert tracing_check.main(["--search", "vtmarkaaa"]) == 0
    assert "no rows" in capsys.readouterr().out


def test_search_exit_code_is_one_on_a_hit_and_names_query_log_tables(monkeypatch, capsys):
    monkeypatch.setenv("AZURE_RESOURCE_GROUP", "rg")
    monkeypatch.setattr(subprocess, "run", fake_az({"vtmarkaaa": {"AppTraces": 2, "LAQueryLogs": 1}, "vtmarkbbb": {}}))
    assert tracing_check.main(["--search", "vtmarkaaa", "vtmarkbbb", "--hours", "2"]) == 1
    out = capsys.readouterr().out
    assert "AppTraces" in out and "2" in out
    assert "logs queries" in out and "no rows" in out


def test_a_hit_only_in_a_query_log_table_is_not_a_leak(monkeypatch, capsys):
    monkeypatch.setenv("AZURE_RESOURCE_GROUP", "rg")
    monkeypatch.setattr(subprocess, "run", fake_az({"vtmarkaaa": {"LAQueryLogs": 1}}))
    assert tracing_check.main(["--search", "vtmarkaaa"]) == 0


def test_a_failed_az_is_an_error_not_no_rows(monkeypatch, capsys):
    monkeypatch.setenv("AZURE_RESOURCE_GROUP", "rg")

    def run(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd, "", "SECRET az text")

    monkeypatch.setattr(subprocess, "run", run)
    assert tracing_check.main(["--search", "vtmarkaaa"]) not in (0, 1)
    captured = capsys.readouterr()
    assert "SECRET" not in captured.out + captured.err and "no rows" not in captured.out


def test_an_unexpected_answer_shape_is_an_error_not_no_rows(monkeypatch, capsys):
    monkeypatch.setenv("AZURE_RESOURCE_GROUP", "rg")

    def run(cmd, **kwargs):
        out = '{"unexpected": 1}' if "--analytics-query" in cmd else "guid\n"
        return subprocess.CompletedProcess(cmd, 0, out, "")

    monkeypatch.setattr(subprocess, "run", run)
    assert tracing_check.main(["--search", "vtmarkaaa"]) not in (0, 1)
    assert "no rows" not in capsys.readouterr().out


@pytest.mark.parametrize("hours", ["0", "-3"])
def test_an_empty_window_is_refused_before_any_subprocess(monkeypatch, hours):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a))
    assert tracing_check.main(["--search", "vtmarkaaa", "--hours", hours]) == 2
    assert calls == []


@pytest.mark.parametrize("listing,expected", [("[]", 3), ('["a"]', 0), ('["a", "b"]', 3)])
def test_the_workspace_must_be_exactly_one(monkeypatch, listing, expected):
    monkeypatch.setenv("AZURE_RESOURCE_GROUP", "rg")

    def run(cmd, **kwargs):
        out = "[]" if "--analytics-query" in cmd else listing
        return subprocess.CompletedProcess(cmd, 0, out, "")

    monkeypatch.setattr(subprocess, "run", run)
    assert tracing_check.main(["--search", "vtmarkaaa"]) == expected


def test_mark_exits_one_unless_every_backend_ended_with_done(monkeypatch):
    class Failing(FakeApi):
        def turn(self, message, **kwargs):
            if self.backend == "hosted":
                return voltrieve_client.Turn(events=[("error", {"code": "boom"})])
            return super().turn(message, **kwargs)

    monkeypatch.setattr(tracing_check, "Api", Failing)
    monkeypatch.setattr(tracing_check, "api_url", lambda: "http://api")
    monkeypatch.setattr(tracing_check, "api_token", lambda: "t")
    assert tracing_check.main(["--mark"]) == 1


def test_a_hit_in_azure_activity_counts(monkeypatch):
    monkeypatch.setenv("AZURE_RESOURCE_GROUP", "rg")
    monkeypatch.setattr(subprocess, "run", fake_az({"vtmarkaaa": {"AzureActivity": 1}}))
    assert tracing_check.main(["--search", "vtmarkaaa"]) == 1
