"""The MCP doors of the API: operations, the car and the customer. Each has its own tokens, tools and rules.

A door exists only when its client id is in the settings. The dealer's door is in the dealer service.
"""

from fastapi import FastAPI
from voltrieve_doors import Door, DoorConfig, Gate
from voltrieve_doors.limits import RateLimit

from app.clients import AZURE_CLI_CLIENT_ID
from app.doors import car, customer, operations
from app.logging_setup import correlation_id
from app.settings import Settings

CALLS_AT_ONCE = 4  # in all doors of this service, so that they cannot slow down Runa


def app_names(text: str, allow_cli: bool) -> dict[str, str]:
    """ "client id=Name;client id=Name", and the Azure CLI when it is allowed."""
    names = {}
    for item in text.split(";"):
        client_id, _, name = item.partition("=")
        if client_id.strip() and name.strip():
            names[client_id.strip()] = name.strip()
    if allow_cli:
        names[AZURE_CLI_CLIENT_ID] = "Azure CLI"
    return names


def door_config(settings: Settings, name: str, client_id: str, scopes: tuple[str, ...]) -> DoorConfig:
    url = f"{settings.mcp_api_url.rstrip('/')}/mcp/{name}"
    return DoorConfig(
        name=name,
        url=url,
        tenant_id=settings.tenant_id,
        client_id=client_id,
        identifier=url if settings.mcp_identifier_is_url else f"api://{client_id}",
        scopes=scopes,
        apps=app_names(settings.mcp_apps, settings.mcp_allow_cli),
        issuer=settings.mcp_issuer,
    )


def build_doors(app: FastAPI, settings: Settings) -> list[Door]:
    gate = Gate(CALLS_AT_ONCE)
    doors = []
    for module, client_id in (
        (operations, settings.mcp_operations_client_id),
        (car, settings.mcp_car_client_id),
        (customer, settings.mcp_customer_client_id),
    ):
        if not (settings.mcp_api_url and client_id):
            continue
        config = door_config(settings, module.NAME, client_id, module.SCOPES)
        doors.append(Door(config, module.tools(app), module.INSTRUCTIONS, gate=gate, rate=RateLimit(30),
                          correlation=correlation_id.get))
    return doors
