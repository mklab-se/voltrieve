"""Typer entry point for the `voltrieve` command."""

from __future__ import annotations

import sys
from enum import Enum
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.text import Text

from . import auth
from .api import ApiClient, ApiError, run_turn
from .config import ConfigError, load_settings
from .render import ASSISTANT_NAME, USER_COLOR, ChatView, clean, gauge_bar, json_line, source_lines
from .tools import tools_for

app = typer.Typer(help="Voltrieve terminal client: chat with Runa, the Voltrieve assistant.", no_args_is_help=True)
err = Console(stderr=True)


class Backend(str, Enum):
    prompt = "prompt"
    loop = "loop"
    hosted = "hosted"


BackendOpt = Annotated[Backend | None, typer.Option(help="Agent backend (X-Agent-Backend).")]
AzOpt = Annotated[bool, typer.Option("--az-token", help="Use `az account get-access-token`.")]
GaugeOpt = Annotated[bool, typer.Option("--gauge", help="Show the gauge and offer the set_gauge tool.")]
DeviceOpt = Annotated[bool, typer.Option("--device-code", help="Device code sign-in.")]


def _fail(message: str) -> typer.Exit:
    err.print(f"[red]{escape(clean(message))}[/red]")
    return typer.Exit(1)


def _client(backend: Backend | None, az_token: bool) -> ApiClient:
    try:
        settings = load_settings()

        def provider() -> str:
            # MSAL refreshes silently from the cache, so long sessions keep a valid token.
            return auth.get_token(settings, az_token=az_token, interactive=False)

        provider()  # fail early with a clear message
    except (ConfigError, auth.AuthError) as exc:
        raise _fail(str(exc))
    return ApiClient(settings.api_url, provider, backend.value if backend else None)


@app.command()
def login(device_code: DeviceOpt = False) -> None:
    """Sign in with Entra (you pick the account) and cache the token."""
    try:
        auth.login(load_settings(), device_code=device_code)
    except (ConfigError, auth.AuthError) as exc:
        raise _fail(str(exc))
    typer.echo("Signed in.")


@app.command()
def logout() -> None:
    """Forget the cached sign-in."""
    try:
        count = auth.logout(load_settings())
    except (ConfigError, auth.AuthError) as exc:
        raise _fail(str(exc))
    typer.echo("Signed out." if count else "Nothing to sign out of.")


@app.command()
def ask(question: str, backend: BackendOpt = None, json_out: Annotated[bool, typer.Option("--json")] = False,
        az_token: AzOpt = False, gauge: GaugeOpt = False) -> None:
    """Ask one question. With --json, print every event as a JSON line."""
    client = _client(backend, az_token)
    out = Console()
    status = {"ok": True}
    sources: list[dict[str, Any]] = []

    def show(name: str, data: dict[str, Any]) -> None:
        if json_out:
            sys.stdout.write(json_line(name, data) + "\n")
            sys.stdout.flush()
        elif name == "delta":
            if not status.get("named"):  # the name goes to stderr, so stdout holds only the answer
                status["named"] = True
                err.print(f"[dim]{ASSISTANT_NAME}[/dim]")
            out.print(clean(data.get("text", "")), end="", markup=False, highlight=False)
        elif name == "sources" and isinstance(data.get("items"), list):
            sources[:] = [i for i in data["items"] if isinstance(i, dict)]
        elif name == "turn_started":
            err.print(f"[dim]correlation id {escape(clean(data.get('correlation_id')))}[/dim]")
        elif name == "tool_ran":
            err.print(f"[dim]client tool {escape(clean(data.get('name')))}: {escape(clean(data.get('output')))}[/dim]")
        elif name == "status":
            err.print(f"[dim]{escape(clean(data.get('message')))}[/dim]")
        elif name == "trace":
            err.print(f"[dim]{escape(clean(data.get('component')))} {escape(clean(data.get('step')))}: {escape(clean(data.get('detail')))}[/dim]")
        elif name == "error":
            err.print(f"[red]error {escape(clean(data.get('code')))}: {escape(clean(data.get('message')))}[/red]")
        if name == "error":
            status["ok"] = False

    try:
        outcome = run_turn(client, question, None, show,
                           banner=lambda t: err.print(f"[bold black on yellow] {escape(clean(t))} [/]"),
                           gauge=lambda v: err.print(gauge_bar(v)), tools=tools_for(gauge))
    except (ApiError, auth.AuthError) as exc:
        raise _fail(str(exc))
    finally:
        client.close()
    if not json_out:
        out.print()
        for line in source_lines(sources, with_open=False):
            err.print(Text(line, style="dim"))
    if outcome.error or not status["ok"]:
        raise typer.Exit(1)


def is_open_command(message: str) -> bool:
    return message == "/open" or message.startswith("/open ")


def open_source(client: ApiClient, items: list[dict[str, Any]], command: str) -> str:
    """/open N: open source N of the last answer in the browser. The link is signed when asked
    for and never printed."""
    import webbrowser

    arg = command.removeprefix("/open").strip()
    if not arg.isdigit():
        return "Use /open with the number of a source, such as /open 1."
    number = int(arg)
    if not 1 <= number <= len(items):
        return f"There is no source {number}. Sources are numbered under the last answer."
    item = items[number - 1]
    title = clean(str(item.get("title") or item.get("id")))
    if not item.get("link"):
        return f"{title} is an internal document and cannot be opened."
    try:
        url = client.source_url(str(item["link"]))
    except (ApiError, auth.AuthError) as exc:
        return str(exc)
    if not webbrowser.open(url):
        return "No browser could be opened here."
    return f"Opened {title} in your browser."


@app.command()
def chat(backend: BackendOpt = None, az_token: AzOpt = False, gauge: GaugeOpt = False,
         trace: Annotated[bool, typer.Option("--trace", help="Print the trace as it happens.")] = False) -> None:
    """Chat in message bubbles. /trace shows the last turn, /gauge turns the gauge on or off, /open N
    opens source N of the last answer. An empty line or Ctrl-D exits."""
    client = _client(backend, az_token)
    console = Console()
    view = ChatView(console, show_trace=trace, gauge_on=gauge)
    conversation_id: str | None = None
    try:
        while True:
            view.print_gauge()
            try:
                message = console.input(f"[bold {USER_COLOR}]› [/]").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not message:
                break
            if message == "/trace":
                view.print_trace()
                continue
            if message == "/gauge":
                view.gauge_on = not view.gauge_on
                continue
            if is_open_command(message):
                console.print(Text(open_source(client, view.last_sources, message), style="dim"))
                continue
            view.erase_input(message)
            view.user(message)
            with view:
                try:
                    outcome = run_turn(client, message, conversation_id, view.on_event,
                                       banner=view.show_banner, gauge=view.set_gauge,
                                       tools=tools_for(view.gauge_on))
                except (ApiError, auth.AuthError) as exc:
                    view.on_event("error", {"code": "client", "message": str(exc)})
                    continue
            conversation_id = outcome.conversation_id or conversation_id
            console.print()
    finally:
        client.close()


if __name__ == "__main__":  # pragma: no cover
    app()
