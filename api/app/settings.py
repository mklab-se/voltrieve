from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """API configuration, read from environment variables of the same (upper case) names."""

    tenant_id: str
    api_client_id: str
    agent_backend: str = "loop"
    cors_origins: str = ""  # comma separated list of allowed origins
    log_level: str = "INFO"
    applicationinsights_connection_string: str = ""
    log_analytics_workspace_id: str = ""  # the workspace holding VoltrieveInsights_CL

    # Needed by the loop backend; read lazily, so the API starts without them.
    foundry_project_endpoint: str = ""
    chat_deployment: str = "gpt-6.1-terra"
    search_endpoint: str = ""
    dealer_url: str = ""
    dealer_client_id: str = ""

    # Source links: the account and the containers whose documents users may open.
    storage_account_name: str = ""
    source_containers: str = ""  # comma separated

    # The app registrations of the three clients, to tell their tokens apart (the azp claim).
    car_client_id: str = ""
    web_client_id: str = ""
    cli_client_id: str = ""

    # Backend A: the names of the three prompt agents the API drives.
    prompt_agent_cli: str = "voltrieve-prompt-cli"
    prompt_agent_web: str = "voltrieve-prompt-web"
    prompt_agent_car: str = "voltrieve-prompt-car"

    # Backend C: the name of the Foundry hosted agent (agents/hosted/deploy.py creates it).
    hosted_agent_name: str = "voltrieve-hosted"

    # The MCP doors (app/doors). A door without a client id does not exist.
    mcp_api_url: str = ""  # https://host, as callers see it
    mcp_operations_client_id: str = ""
    mcp_car_client_id: str = ""
    mcp_customer_client_id: str = ""
    mcp_apps: str = ""  # the harnesses that may call: "client id=Name;client id=Name"
    mcp_allow_cli: bool = False  # the Azure CLI too, for scripts
    mcp_identifier_is_url: bool = False  # the address is the Application ID URI, else api://<client id>
    mcp_issuer: str = ""  # another authorization server to point to

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]
