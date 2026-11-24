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
