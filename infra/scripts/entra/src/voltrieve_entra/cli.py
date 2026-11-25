import argparse
import base64
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from azure.identity import AzureCliCredential

from .apps import SPA_APPS, ensure_app_role_assignment, ensure_spa_redirect, setup
from .cities import check_cities, dealer_cities
from .doors import setup_doors
from .graph import GraphClient

GRAPH_SCOPE = "https://graph.microsoft.com/.default"
TOKEN_REFRESH_MARGIN_SECONDS = 300
FIRST_CITY = "Göteborg"  # the deployer's, when nobody has a city at the dealer door
HOST = re.compile(r"[a-z0-9-]+(\.[a-z0-9-]+)+")


def _graph() -> tuple[GraphClient, str]:
    credential = AzureCliCredential()
    cached = credential.get_token(GRAPH_SCOPE)

    def provider() -> str:
        # AzureCliCredential spawns az on every call, so reuse the token until shortly before expiry.
        nonlocal cached
        if cached.expires_on - time.time() < TOKEN_REFRESH_MARGIN_SECONDS:
            cached = credential.get_token(GRAPH_SCOPE)
        return cached.token

    return GraphClient(provider), cached.token


def _tenant_id(token: str) -> str:
    """Read the tid claim of the already trusted az cli token (no verification needed)."""
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))["tid"]


def _azd_env_set(key: str, value: str) -> None:
    subprocess.run(["azd", "env", "set", key, value], check=True)


def _print_password(password: str) -> None:
    print(
        "\nTest user password (shown once, not stored anywhere, save it now):\n"
        f"  {password}\n",
        file=sys.stdout,
    )


def _host(text: str) -> str:
    """A host name and nothing else, as in api.example.com. Empty is none."""
    if text and not HOST.fullmatch(text):
        raise argparse.ArgumentTypeError("a host name in lower case, without https:// and a path")
    return text


def _print_door(name: str, identifiers: list[str]) -> None:
    print(f"{name}: {', '.join(identifiers)}")


def _check_cities(args: argparse.Namespace) -> None:
    """Before anything is changed. A city that the dealer does not know would leave that caller out."""
    try:
        check_cities(args.dealer_cities or f"deployer={FIRST_CITY}", dealer_cities(Path(args.dealer_data)))
    except (OSError, ValueError, KeyError) as exc:
        sys.exit(f"preprovision: DEALER_DOOR_CITIES: {exc}")


def _run_setup(args: argparse.Namespace) -> None:
    if args.mcp:
        _check_cities(args)
    g, token = _graph()
    me = g.get("/me?$select=id") or {}
    env = setup(
        g,
        _tenant_id(token),
        me["id"],
        args.verified_domain,
        args.web_redirect_uri,
        on_password=_print_password,
        car_redirect_uris=args.car_redirect_uri,
        dashboard_redirect_uris=args.dashboard_redirect_uri,
        dealer_redirect_uris=args.dealer_redirect_uri,
    )
    if args.mcp:
        env |= setup_doors(
            g,
            me["id"],
            env["TEST_USER_UPN"],
            api_host=args.api_host,
            dealer_host=args.dealer_host,
            allow_cli=args.allow_cli,
            on_door=_print_door,
        )
        for service in ("API", "DEALER"):
            print(f"The address is an identifier, {service}: {env[f'MCP_{service}_IDENTIFIER_IS_URL']}")
        if not args.dealer_cities:  # the dealer door refuses everyone who is not listed
            env["DEALER_DOOR_CITIES"] = f"{me['id']}={FIRST_CITY}"
    for key, value in env.items():
        _azd_env_set(key, value)


def _run_assign(args: argparse.Namespace) -> None:
    g, _ = _graph()
    ensure_app_role_assignment(
        g, args.principal_object_id, args.resource_sp_object_id, args.app_role_id
    )


def _run_spa_redirect(args: argparse.Namespace) -> None:
    g, _ = _graph()
    ensure_spa_redirect(g, args.uri, app=args.app)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="voltrieve-entra")
    sub = parser.add_subparsers(dest="command", required=True)

    p_setup = sub.add_parser("setup", help="Create or update the Voltrieve app registrations")
    p_setup.add_argument("--verified-domain", required=True)
    p_setup.add_argument("--web-redirect-uri", action="append", default=[])
    p_setup.add_argument("--car-redirect-uri", action="append", default=[])
    p_setup.add_argument("--dashboard-redirect-uri", action="append", default=[])
    p_setup.add_argument("--dealer-redirect-uri", action="append", default=[])
    p_setup.add_argument("--mcp", action="store_true", help="The MCP doors and the harnesses too")
    p_setup.add_argument("--api-host", type=_host, default="", help="The host of the API's doors")
    p_setup.add_argument("--dealer-host", type=_host, default="", help="The host of the dealer's door")
    p_setup.add_argument("--allow-cli", action="store_true", help="The Azure CLI may ask for tokens for the doors")
    p_setup.add_argument("--dealer-cities", default="", help="Who has which dealer city: 'object id=City;...'")
    p_setup.add_argument("--dealer-data", default="services/dealer/app/data.json", help="The cities of the dealers")
    p_setup.set_defaults(func=_run_setup)

    p_assign = sub.add_parser("assign-app-role", help="Assign an app role to a principal")
    p_assign.add_argument("--principal-object-id", required=True)
    p_assign.add_argument("--resource-sp-object-id", required=True)
    p_assign.add_argument("--app-role-id", required=True)
    p_assign.set_defaults(func=_run_assign)

    p_spa = sub.add_parser("set-spa-redirect", help="Add a redirect URI to a browser app registration")
    p_spa.add_argument("--uri", required=True)
    p_spa.add_argument("--app", choices=list(SPA_APPS), default="web")
    p_spa.set_defaults(func=_run_spa_redirect)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
