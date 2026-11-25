"""Which client a conversation and a turn came from, and what a conversation is marked with.

The client is read from the `azp` claim of the token. The test header can only take a conversation
out of the counting (client `test`), never put one in. Only the car model slug from the catalog is
taken from what the client says about the car, so nothing about a person or a car is stored.
"""

from voltrieve_agent.server_tools import MODEL_SLUGS

from app.settings import Settings

AZURE_CLI_CLIENT_ID = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"
CAR = "car"
MCP = "mcp"  # ask_runa of the customer's door (app/doors/customer.py), which no token of the API can be


def is_test(header: str | None) -> bool:
    return header == "1"


def client_name(settings: Settings, azp: str, test: bool) -> str:
    if test:
        return "test"
    for name, client_id in (
        ("car", settings.car_client_id),
        ("web", settings.web_client_id),
        ("cli", settings.cli_client_id),
    ):
        if client_id and azp == client_id:  # an empty setting never matches an empty azp
            return name
    return "other"


def ordering_client(settings: Settings, azp: str, header: str | None) -> str:
    """The client that decides who may order a car. Not the one in the usage count: a test is no client
    here, and the Azure CLI, which our scripts sign in with, counts as the CLI. The header can only
    turn a client into the car, which takes the orders away and never gives them."""
    if header == CAR:
        return CAR
    return "cli" if azp == AZURE_CLI_CLIENT_ID else client_name(settings, azp, False)


def continuing_client(began: str, continues: str) -> str:
    """The ordering client of a turn that goes on: the car, when it had any part in the turn."""
    return CAR if CAR in (began, continues) else continues


def car_model_slug(model: str | None) -> str | None:
    """The catalog slug for the last word of a model name ("Voltrieve Spark" is "spark")."""
    words = (model or "").lower().split()
    return words[-1] if words and words[-1] in MODEL_SLUGS else None


def conversation_metadata(owner_oid: str, client: str, backend: str, car_model: str | None) -> dict[str, str]:
    metadata = {"owner_oid": owner_oid, "client": client, "backend": backend}
    slug = car_model_slug(car_model)
    if slug:
        metadata["car_model"] = slug
    return metadata
