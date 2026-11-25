import unicodedata
from pathlib import Path

import pytest

from voltrieve_entra import apps, cli, doors

DOMAIN = "contoso.onmicrosoft.com"
CLI = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"
NAMES = ["operations", "car", "customer", "runa", "dealer"]
HARNESS_NAMES = ["voltrieve-harness-claude", "voltrieve-harness-chatgpt", "voltrieve-harness-vscode",
                 "voltrieve-harness-copilot"]
VERIFIED = [{"id": "contoso.com", "isVerified": True}, {"id": "fabrikam.com", "isVerified": False}]


def _setup(graph, **kwargs):
    env = apps.setup(graph, "tid", "deployer-oid", DOMAIN, [])
    return env, doors.setup_doors(graph, "deployer-oid", env["TEST_USER_UPN"], **kwargs)


def _app(graph_mock, name):
    return next(a for a in graph_mock.applications.values() if a["displayName"] == name)


def _sp(graph_mock, name):
    app_id = _app(graph_mock, name)["appId"]
    return next(s for s in graph_mock.service_principals.values() if s["appId"] == app_id)


def _is_url(env):
    return env["MCP_API_IDENTIFIER_IS_URL"], env["MCP_DEALER_IDENTIFIER_IS_URL"]


def _door(name):
    return next(d for d in doors.DOORS if d.name == name)


@pytest.mark.parametrize("name,scopes,roles", [
    ("operations", ["operations.read"], {"Dashboard.View": "User"}),
    ("car", ["car.use"], {"Car.Remote": "User"}),
    ("customer", ["customer.tools"], {"Customer": "User", "TestDrive.Book": "User"}),
    ("runa", ["runa.ask"], {"Customer": "User", "TestDrive.Book": "User"}),
    ("dealer", ["dealer.use"], {"Dealer.Staff": "User", "Dealer.Agent": "Application"}),
])
def test_each_door_has_its_scopes_and_roles(graph_mock, graph, name, scopes, roles):
    _setup(graph)
    app = _app(graph_mock, f"voltrieve-mcp-{name}")
    assert app["signInAudience"] == "AzureADMyOrg"
    assert app["api"]["requestedAccessTokenVersion"] == 2
    assert [s["value"] for s in app["api"]["oauth2PermissionScopes"]] == scopes
    assert all(s["isEnabled"] and s["type"] == "User" for s in app["api"]["oauth2PermissionScopes"])
    assert {r["value"]: r["allowedMemberTypes"] for r in app["appRoles"]} == {v: [k] for v, k in roles.items()}
    assert not app.get("isFallbackPublicClient") and "publicClient" not in app


def test_ids_are_pinned_and_unique():
    ids = [d.scope_id(s.value) for d in doors.DOORS for s in d.scopes]
    ids += [d.role_id(r.value) for d in doors.DOORS for r in d.roles]
    assert len(set(ids)) == len(ids) == 13
    assert not set(ids) & {apps.stable_id("role:Dashboard.View"), apps.stable_id("role:TestDrive.Book")}
    assert _door("operations").scope_id("operations.read") == "52f450d4-7147-5bc9-a7f9-9ae21a79f33a"


def test_setup_twice_yields_same_ids_and_no_duplicates(graph_mock, graph):
    _, first = _setup(graph, api_host="api.contoso.com", allow_cli=True)
    count = (len(graph_mock.applications), len(graph_mock.service_principals), len(graph_mock.assignments),
             len(graph_mock.grants))
    _, second = _setup(graph, api_host="api.contoso.com", allow_cli=True)
    assert first == second
    assert count == (len(graph_mock.applications), len(graph_mock.service_principals), len(graph_mock.assignments),
                     len(graph_mock.grants))
    assert len(graph_mock.applications) == 7 + 5 + 4


def test_outputs(graph_mock, graph):
    _, env = _setup(graph)
    assert set(env) == {f"MCP_{n.upper()}_CLIENT_ID" for n in NAMES} | {
        "MCP_APPS", "MCP_API_IDENTIFIER_IS_URL", "MCP_DEALER_IDENTIFIER_IS_URL"}
    for name in NAMES:
        assert env[f"MCP_{name.upper()}_CLIENT_ID"] == _app(graph_mock, f"voltrieve-mcp-{name}")["appId"]
    ids = [_app(graph_mock, n)["appId"] for n in HARNESS_NAMES]
    names = ["Claude", "ChatGPT", "VS Code", "Microsoft 365 Copilot"]
    assert env["MCP_APPS"] == ";".join(f"{i}={n}" for i, n in zip(ids, names, strict=True))


@pytest.mark.parametrize("name", NAMES)
def test_only_the_api_identifier_without_a_host(graph_mock, graph, name):
    _, env = _setup(graph)
    app = _app(graph_mock, f"voltrieve-mcp-{name}")
    assert app["identifierUris"] == [f"api://{app['appId']}"]
    assert _is_url(env) == ("false", "false")
    assert graph_mock.calls("GET", "/domains") == 0


def test_the_address_is_an_identifier_under_a_verified_domain(graph_mock, graph):
    graph_mock.domains = VERIFIED
    seen = {}
    _, env = _setup(graph, api_host="api.volt.contoso.com", dealer_host="contoso.com", on_door=seen.__setitem__)
    for name in NAMES:
        app = _app(graph_mock, f"voltrieve-mcp-{name}")
        host = "contoso.com" if name == "dealer" else "api.volt.contoso.com"
        assert app["identifierUris"] == [f"api://{app['appId']}", f"https://{host}/mcp/{name}"]
        assert seen[f"voltrieve-mcp-{name}"] == app["identifierUris"]
    assert _is_url(env) == ("true", "true")


@pytest.mark.parametrize("host", [
    "api.fabrikam.com",  # not verified
    "api.notcontoso.com",  # ends the same, but is another domain
    "contoso.com.evil.example",
    "ca-api.region.azurecontainerapps.io",
])
def test_a_host_outside_the_verified_domains_is_no_identifier(graph_mock, graph, host):
    graph_mock.domains = VERIFIED
    _, env = _setup(graph, api_host=host, dealer_host="dealer.contoso.com")
    app = _app(graph_mock, "voltrieve-mcp-operations")
    assert app["identifierUris"] == [f"api://{app['appId']}"]
    assert _is_url(env) == ("false", "true")


def test_each_service_has_a_host_of_its_own(graph_mock, graph):
    graph_mock.domains = VERIFIED
    _, env = _setup(graph, api_host="api.contoso.com")
    assert _is_url(env) == ("true", "false")
    assert len(_app(graph_mock, "voltrieve-mcp-car")["identifierUris"]) == 2
    assert len(_app(graph_mock, "voltrieve-mcp-dealer")["identifierUris"]) == 1


def test_a_host_that_is_taken_away_is_removed(graph_mock, graph):
    graph_mock.domains = VERIFIED
    _setup(graph, api_host="api.contoso.com")
    _setup(graph)
    assert len(_app(graph_mock, "voltrieve-mcp-car")["identifierUris"]) == 1


def test_every_door_requires_an_assignment(graph_mock, graph):
    _setup(graph)
    for name in NAMES:
        assert _sp(graph_mock, f"voltrieve-mcp-{name}")["appRoleAssignmentRequired"] is True


def test_the_deployer_gets_every_user_role_and_the_tester_only_customer(graph_mock, graph):
    env, _ = _setup(graph)
    door_roles = {d.role_id(r.value): r for d in doors.DOORS for r in d.roles}
    mine = graph_mock.role_assignments_for("deployer-oid") & set(door_roles)
    assert mine == {i for i, r in door_roles.items() if r.kind == "User"} and len(mine) == 7
    tester = graph_mock.role_assignments_for(graph_mock.user_id(env["TEST_USER_UPN"]))
    assert tester == {
        env["CHAT_USER_ROLE_ID"], _door("customer").role_id("Customer"), _door("runa").role_id("Customer")}
    agent = _door("dealer").role_id("Dealer.Agent")
    assert agent not in {a["appRoleId"] for a in graph_mock.assignments}


@pytest.mark.parametrize("name", HARNESS_NAMES)
def test_a_harness_is_a_public_client_without_a_secret(graph_mock, graph, name):
    _, env = _setup(graph)
    app = _app(graph_mock, name)
    platform = "spa" if name == "voltrieve-harness-copilot" else "publicClient"
    assert app["isFallbackPublicClient"] is (platform == "publicClient") and app["signInAudience"] == "AzureADMyOrg"
    assert {"passwordCredentials", "web", "spa", "publicClient"} & set(app) == {platform}
    asked = {r["resourceAppId"]: [a["id"] for a in r["resourceAccess"]] for r in app["requiredResourceAccess"]}
    assert asked == {
        env[f"MCP_{d.name.upper()}_CLIENT_ID"]: [d.scope_id(s.value) for s in d.scopes] for d in doors.DOORS
    }


def test_redirect_addresses(graph_mock, graph):
    _setup(graph)
    found = {n: (_app(graph_mock, n).get("publicClient") or _app(graph_mock, n)["spa"])["redirectUris"]
             for n in HARNESS_NAMES}
    assert found == {
        "voltrieve-harness-claude": [
            "https://claude.ai/api/mcp/auth_callback", "http://localhost", "http://localhost/callback",
            "http://127.0.0.1", "http://127.0.0.1/callback"],
        "voltrieve-harness-chatgpt": ["https://chatgpt.com/connector_platform_oauth_redirect"],
        "voltrieve-harness-vscode": ["http://127.0.0.1:33418", "https://vscode.dev/redirect"],
        "voltrieve-harness-copilot": ["https://teams.microsoft.com/api/platform/v1.0/oAuthRedirect"],
    }


@pytest.mark.parametrize("name,platform", [("voltrieve-harness-chatgpt", "publicClient"),
                                           ("voltrieve-harness-copilot", "spa")])
def test_a_redirect_address_added_by_hand_stays(graph_mock, graph, name, platform):
    _setup(graph)
    (first,) = _app(graph_mock, name)[platform]["redirectUris"]
    added = "https://chatgpt.com/connector/oauth/made-up"
    _app(graph_mock, name)[platform]["redirectUris"].append(added)
    for _ in range(2):
        _setup(graph)
        assert _app(graph_mock, name)[platform]["redirectUris"] == [first, added]


def test_the_harnesses_have_consent_for_the_doors_and_nothing_else(graph_mock, graph):
    _setup(graph)
    sps = {s["id"]: next(a["displayName"] for a in graph_mock.applications.values() if a["appId"] == s["appId"])
           for s in graph_mock.service_principals.values()}
    found = {(sps[g["clientId"]], sps[g["resourceId"]], g["consentType"], g["scope"])
             for g in graph_mock.grants if "harness" in sps[g["clientId"]]}
    scopes = {"operations": "operations.read", "car": "car.use", "customer": "customer.tools",
              "runa": "runa.ask", "dealer": "dealer.use"}
    assert found == {(h, f"voltrieve-mcp-{d}", "AllPrincipals", s) for h in HARNESS_NAMES for d, s in scopes.items()}


@pytest.mark.parametrize("name", NAMES)
def test_the_azure_cli_is_pre_authorized_only_when_allowed(graph_mock, graph, name):
    _setup(graph, allow_cli=True)
    app = _app(graph_mock, f"voltrieve-mcp-{name}")
    ids = [s["id"] for s in app["api"]["oauth2PermissionScopes"]]
    assert app["api"]["preAuthorizedApplications"] == [{"appId": CLI, "delegatedPermissionIds": ids}]
    _setup(graph)  # and it is taken back
    assert app["api"]["preAuthorizedApplications"] == []
    assert app["api"]["requestedAccessTokenVersion"] == 2 and len(app["api"]["oauth2PermissionScopes"]) == len(ids)


def test_the_doors_change_nothing_that_was_there(graph_mock, graph):
    env = apps.setup(graph, "tid", "deployer-oid", DOMAIN, [])
    before = {a["displayName"]: dict(a) for a in graph_mock.applications.values()}
    grants = list(graph_mock.grants)
    doors.setup_doors(graph, "deployer-oid", env["TEST_USER_UPN"])
    assert {n: a for n, a in ((a["displayName"], a) for a in graph_mock.applications.values()) if n in before} == before
    assert graph_mock.grants[: len(grants)] == grants


def test_a_fresh_door_is_retried(graph_mock, graph):
    env = apps.setup(graph, "tid", "deployer-oid", DOMAIN, [])
    graph_mock.fail_next("PATCH", "/applications", 404, times=3)
    graph_mock.fail_next("GET", "/users", 404, times=1)
    sleeps: list[float] = []
    doors.setup_doors(graph, "deployer-oid", env["TEST_USER_UPN"], sleep=sleeps.append)
    assert len(sleeps) == 4


IS_URL = {"MCP_API_IDENTIFIER_IS_URL": "true", "MCP_DEALER_IDENTIFIER_IS_URL": "false"}


def _run(monkeypatch, *args):
    calls = []
    env = {"TEST_USER_UPN": "t@x"}
    monkeypatch.setattr(cli, "_graph", lambda: (type("G", (), {"get": lambda s, p: {"id": "me"}})(), "t"))
    monkeypatch.setattr(cli, "_tenant_id", lambda t: "tid")
    monkeypatch.setattr(cli, "setup", lambda *a, **k: dict(env))
    monkeypatch.setattr(cli, "setup_doors", lambda g, *a, **k: calls.append((a, k)) or dict(IS_URL))
    saved = {}
    monkeypatch.setattr(cli, "_azd_env_set", saved.__setitem__)
    data = Path(__file__).parents[4] / "services/dealer/app/data.json"
    cli.main(["setup", "--verified-domain", DOMAIN, "--dealer-data", str(data), *args])
    return calls, saved


def test_without_the_flag_there_are_no_doors(monkeypatch):
    calls, saved = _run(monkeypatch, "--api-host", "api.contoso.com", "--allow-cli")
    assert calls == [] and saved == {"TEST_USER_UPN": "t@x"}


def test_the_command_line_passes_the_flags(monkeypatch, capsys):
    calls, saved = _run(monkeypatch, "--mcp", "--allow-cli", "--api-host", "api.contoso.com", "--dealer-host", "")
    ((args, options),) = calls
    assert args == ("me", "t@x")
    assert (options["api_host"], options["dealer_host"], options["allow_cli"]) == ("api.contoso.com", "", True)
    assert saved == {"TEST_USER_UPN": "t@x", "DEALER_DOOR_CITIES": "me=Göteborg"} | IS_URL
    out = capsys.readouterr().out
    assert "API: true" in out and "DEALER: false" in out


@pytest.mark.parametrize("host", ["https://api.contoso.com", "api.contoso.com/mcp", "API.contoso.com", "api", "a.b:8000"])
def test_the_command_line_refuses_what_is_no_host(monkeypatch, host):
    with pytest.raises(SystemExit):
        _run(monkeypatch, "--mcp", "--api-host", host)


@pytest.mark.parametrize("cities", ["a=Malmö", " a = Malmö ; b=Göteborg;", unicodedata.normalize("NFD", "a=Göteborg")])
def test_a_list_of_cities_is_kept(monkeypatch, cities):
    calls, saved = _run(monkeypatch, "--mcp", "--dealer-cities", cities)
    assert calls and "DEALER_DOOR_CITIES" not in saved


@pytest.mark.parametrize("cities,text", [
    ("secret-oid=Atlantis", "there is no dealer in Atlantis. The dealers are in Stockholm"),
    ("a=Malmö;secret-oid", "entry 2 must be"),
    ("=Malmö", "entry 1 must be"),
    ("secret-oid=", "entry 1 must be"),
    ("secret-oid=Malmö;secret-oid=Uppsala", "entry 2 has the same object id"),
    ("a=malmö", "there is no dealer in malmö"),
])
def test_a_wrong_list_of_cities_stops_before_anything_is_changed(monkeypatch, cities, text):
    with pytest.raises(SystemExit) as err:
        _run(monkeypatch, "--mcp", "--dealer-cities", cities)
    assert "DEALER_DOOR_CITIES" in str(err.value) and text in str(err.value) and "secret-oid" not in str(err.value)
    monkeypatch.setattr(cli, "_graph", lambda: pytest.fail("the check comes first"))
    with pytest.raises(SystemExit):
        cli.main(["setup", "--verified-domain", DOMAIN, "--mcp", "--dealer-cities", "x", "--dealer-data", "nowhere"])


def test_without_the_doors_the_list_is_not_read(monkeypatch):
    calls, saved = _run(monkeypatch, "--dealer-cities", "a=Atlantis")
    assert calls == [] and saved == {"TEST_USER_UPN": "t@x"}


def test_a_scope_and_a_role_of_a_door_never_have_the_same_name():
    """Entra refuses it, also when only the case differs."""
    for door in doors.DOORS:
        names = [x.value.lower() for x in (*door.scopes, *door.roles)]
        assert len(set(names)) == len(names)
