"""Which client a conversation and a turn came from, and what a conversation is marked with.

The client is read from the `azp` claim of the token. The test header can only take a conversation
out of the counting (client `test`), never put one in. Only the car model slug from the catalog is
taken from what the client says about the car, so nothing about a person or a car is stored.
"""

from voltrieve_agent.server_tools import MODEL_SLUGS

from app.settings import Settings

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
