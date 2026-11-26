"""The MCP doors and the harnesses that may call them. Only with --mcp, so nothing here runs otherwise."""

import time
from collections.abc import Callable
from dataclasses import dataclass

from .apps import (
    AZURE_CLI_CLIENT_ID,
    Sleep,
    _app_role,
    _retrying,
    ensure_app_role_assignment,
    ensure_application,
    ensure_oauth2_grant,
    ensure_service_principal,
    stable_id,
)
from .graph import GraphClient


@dataclass(frozen=True)
class Scope:
    value: str
    text: str  # "Allows the app to <text>"


@dataclass(frozen=True)
class Role:
    value: str
    description: str
    kind: str = "User"  # or Application


@dataclass(frozen=True)
class DoorApp:
    name: str
    host: str  # which service it is in: api or dealer
    scopes: tuple[Scope, ...]
    roles: tuple[Role, ...]

    @property
    def display_name(self) -> str:
        return f"voltrieve-mcp-{self.name}"

    # The ids belong to the door. The same role name on the API or on another door is another role.
    def scope_id(self, value: str) -> str:
        return stable_id(f"mcp-{self.name}:scope:{value}")

    def role_id(self, value: str) -> str:
        return stable_id(f"mcp-{self.name}:role:{value}")


DOORS = (
    DoorApp(
        "operations",
        "api",
        (Scope("operations.read", "read how Voltrieve behaves, without any chat text"),),
        (Role("Dashboard.View", "Can read operations through the MCP door."),),
    ),
    DoorApp(
        "car",
        "api",
        (Scope("car.use", "honk, flash the lights and set the wipers of your own parked car"),),
        (Role("Car.Remote", "Can reach their own car through the MCP door."),),
    ),
    DoorApp(
        "customer",
        "api",
        (Scope("customer.tools", "search documents and parts, see stock and prices, and book a test drive"),),
        (
            Role("Customer", "Can use the customer's tools through the MCP door."),
            Role("TestDrive.Book", "Can book test drives through the MCP door."),
        ),
    ),
    DoorApp(
        "runa",
        "api",
        (Scope("runa.ask", "ask Runa"),),
        (
            Role("Customer", "Can ask Runa through the MCP door."),
            Role("TestDrive.Book", "Can book test drives through Runa."),
        ),
    ),
    DoorApp(
        "dealer",
        "dealer",
        (Scope("dealer.use", "manage the stock and the orders of your own dealer"),),
        (
            Role("Dealer.Staff", "Can manage their own dealer through the MCP door."),
            Role("Dealer.Agent", "An application that can manage the stock of one dealer.", "Application"),
        ),
    ),
)


@dataclass(frozen=True)
class Harness:
    name: str  # as a person knows it
    display_name: str
    redirect_uris: tuple[str, ...]
    platform: str = "publicClient"  # or spa, or web


# All are public clients with PKCE, so none has a secret. A hosted harness is under "mobile and desktop" too, except
# Copilot, which signs in without a secret only as a single page application. Copilot Studio needs a confidential
# web client: it gets no secret here, but a federated credential from its connector's managed identity, added by
# hand with the connector's own redirect address (docs/bring-your-own-ai.md). Entra ignores the port of a
# localhost address but not its path, and Claude Code calls back on /callback.
HARNESSES = (
    Harness(
        "Claude",
        "voltrieve-harness-claude",
        (
            "https://claude.ai/api/mcp/auth_callback",
            "http://localhost",
            "http://localhost/callback",
            "http://127.0.0.1",
            "http://127.0.0.1/callback",
        ),
    ),
    Harness("ChatGPT", "voltrieve-harness-chatgpt", ("https://chatgpt.com/connector_platform_oauth_redirect",)),
    Harness("VS Code", "voltrieve-harness-vscode", ("http://127.0.0.1:33418", "https://vscode.dev/redirect")),
    Harness(
        "Microsoft 365 Copilot",
        "voltrieve-harness-copilot",
        ("https://teams.microsoft.com/api/platform/v1.0/oAuthRedirect",),
        platform="spa",
    ),
    Harness("Copilot Studio", "voltrieve-harness-copilot-studio", (), platform="web"),
)


# Application bodies


def _scope(door: DoorApp, scope: Scope) -> dict:
    return {
        "id": door.scope_id(scope.value),
        "value": scope.value,
        "type": "User",
        "isEnabled": True,
        "adminConsentDisplayName": f"Voltrieve: {scope.value}",
        "adminConsentDescription": f"Allows the app to {scope.text}, as the signed-in user.",
        "userConsentDisplayName": f"Voltrieve: {scope.value}",
        "userConsentDescription": f"Allows the app to {scope.text}, as you.",
    }


def door_application_body(door: DoorApp) -> dict:
    return {
        "signInAudience": "AzureADMyOrg",
        "api": {
            "requestedAccessTokenVersion": 2,
            "oauth2PermissionScopes": [_scope(door, scope) for scope in door.scopes],
        },
        "appRoles": [
            _app_role(door.role_id(role.value), role.value, role.description, [role.kind]) for role in door.roles
        ],
    }


def door_pre_authorized_body(door: DoorApp, allow_cli: bool) -> dict:
    """Lets the Azure CLI ask for the scopes of the door, or takes that back. The door has its own list too."""
    scope_ids = [door.scope_id(scope.value) for scope in door.scopes]
    allowed = [{"appId": AZURE_CLI_CLIENT_ID, "delegatedPermissionIds": scope_ids}] if allow_cli else []
    # The rest of api is repeated so that PATCH semantics can never drop it.
    return {"api": {**door_application_body(door)["api"], "preAuthorizedApplications": allowed}}


def harness_application_body(
    harness: Harness, door_client_ids: dict[str, str], known_uris: list[str] | None = None
) -> dict:
    """`known_uris` are the ones already on the registration: some are added by hand, so they stay."""
    uris = [*harness.redirect_uris, *(u for u in known_uris or [] if u not in harness.redirect_uris)]
    return {
        "signInAudience": "AzureADMyOrg",
        "isFallbackPublicClient": harness.platform == "publicClient",
        harness.platform: {"redirectUris": uris},
        "requiredResourceAccess": [
            {
                "resourceAppId": door_client_ids[door.name],
                "resourceAccess": [
                    {"id": door.scope_id(scope.value), "type": "Scope"} for scope in door.scopes
                ],
            }
            for door in DOORS
        ],
    }


def known_redirect_uris(g: GraphClient, harness: Harness) -> list[str]:
    found = g.get(f"/applications?$filter=displayName eq '{harness.display_name}'") or {}
    return [
        uri
        for app in found.get("value", [])
        for uri in (app.get(harness.platform) or {}).get("redirectUris", [])
    ]


# The address as an identifier


def verified_domains(g: GraphClient) -> list[str]:
    found = g.get("/domains?$select=id,isVerified") or {}
    return [d["id"].lower() for d in found.get("value", []) if d.get("isVerified") is True]


def address(door: DoorApp, host: str, domains: list[str]) -> str | None:
    """The https address of the door, when Entra ID lets it be an identifier: on a verified domain, or under one."""
    host = host.lower()
    if host and any(host == domain or host.endswith(f".{domain}") for domain in domains):
        return f"https://{host}/mcp/{door.name}"
    return None


def setup_doors(
    g: GraphClient,
    deployer_object_id: str,
    tester_upn: str,
    *,
    api_host: str = "",
    dealer_host: str = "",
    allow_cli: bool = False,
    sleep: Sleep = time.sleep,
    on_door: Callable[[str, list[str]], None] | None = None,
) -> dict[str, str]:
    """Create or update the doors and the harnesses. `on_door` gets the identifiers that each door was given."""
    hosts = {"api": api_host, "dealer": dealer_host}
    domains = verified_domains(g) if any(hosts.values()) else []
    tester = _retrying(lambda: g.get(f"/users/{tester_upn}"), sleep)
    assert tester is not None
    client_ids: dict[str, str] = {}
    sp_ids: dict[str, str] = {}
    without_url: set[str] = set()  # the hosts with a door that has no address as its identifier
    for door in DOORS:
        app = ensure_application(g, door.display_name, door_application_body(door), sleep)
        client_ids[door.name] = app["appId"]
        url = address(door, hosts[door.host], domains)
        if url is None:
            without_url.add(door.host)
        identifiers = [f"api://{app['appId']}"] + ([url] if url else [])
        # After the first PATCH: the app takes v2 tokens by then, and its scopes exist.
        for body in ({"identifierUris": identifiers}, door_pre_authorized_body(door, allow_cli)):
            _retrying(lambda body=body: g.patch(f"/applications/{app['id']}", body), sleep)
        if on_door is not None:
            on_door(door.display_name, identifiers)

        sp_id = sp_ids[door.name] = ensure_service_principal(g, app["appId"], sleep)["id"]
        _retrying(lambda: g.patch(f"/servicePrincipals/{sp_id}", {"appRoleAssignmentRequired": True}), sleep)
        for role in door.roles:
            if role.kind == "User":
                ensure_app_role_assignment(g, deployer_object_id, sp_id, door.role_id(role.value), sleep)
            if role.value == "Customer":  # all that the test user gets
                ensure_app_role_assignment(g, tester["id"], sp_id, door.role_id(role.value), sleep)

    apps = []
    for harness in HARNESSES:
        body = harness_application_body(harness, client_ids, known_redirect_uris(g, harness))
        app = ensure_application(g, harness.display_name, body, sleep)
        harness_sp = ensure_service_principal(g, app["appId"], sleep)
        for door in DOORS:
            scopes = " ".join(scope.value for scope in door.scopes)
            ensure_oauth2_grant(g, harness_sp["id"], sp_ids[door.name], scopes, sleep)
        apps.append(f"{app['appId']}={harness.name}")

    return {f"MCP_{name.upper()}_CLIENT_ID": client_id for name, client_id in client_ids.items()} | {
        "MCP_APPS": ";".join(apps),
        "MCP_API_IDENTIFIER_IS_URL": "false" if "api" in without_url else "true",
        "MCP_DEALER_IDENTIFIER_IS_URL": "false" if "dealer" in without_url else "true",
    }
