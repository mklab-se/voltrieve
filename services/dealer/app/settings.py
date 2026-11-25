from typing import Literal

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Dealer service configuration, read from the environment variables of the same names. The table
    store needs TABLE_ENDPOINT. The memory store is used only when STORE is "memory"."""

    tenant_id: str
    dealer_client_id: str
    log_level: str = "INFO"
    applicationinsights_connection_string: str = ""
    store: Literal["table", "memory"] = "table"
    table_endpoint: str = ""
    table_name: str = "dealer"
    site_client_id: str = ""
    site_dir: str = "/srv/site"  # the built page

    # The MCP door (app/door.py). Without an address and a client id there is none.
    mcp_dealer_url: str = ""  # https://host, as callers see it
    mcp_dealer_client_id: str = ""
    mcp_apps: str = ""  # the harnesses that may call: "client id=Name;client id=Name"
    mcp_allow_cli: bool = False  # the Azure CLI too, for scripts
    mcp_identifier_is_url: bool = False  # the address is the Application ID URI, else api://<client id>
    mcp_issuer: str = ""  # another authorization server to point to
    dealer_door_cities: str = ""  # whose city: "oid=City;oid=City", of people and applications
