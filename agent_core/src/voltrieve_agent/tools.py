"""Tool definitions and the tool set that validates, authorizes and runs them."""

import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field

from jsonschema import Draft202012Validator, FormatChecker

from voltrieve_agent.events import WORKING, Emit, trace
from voltrieve_agent.private import current_private, log_exception

logger = logging.getLogger(__name__)

# Set by ToolSet.execute while a handler runs, so handlers can emit trace events.
current_emit: ContextVar[Emit | None] = ContextVar("current_emit", default=None)
# Set by the loop to the turn's correlation id, sent to downstream services as X-Correlation-Id.
current_correlation_id: ContextVar[str | None] = ContextVar("current_correlation_id", default=None)
# Set by the loop to the id of the turn, which a paused turn keeps when it goes on.
current_turn_id: ContextVar[str | None] = ContextVar("current_turn_id", default=None)


@dataclass(frozen=True)
class UserContext:
    oid: str
    username: str
    roles: frozenset[str]
    name: str = field(default="", repr=False)  # the customer's name on an order, for no log
    client: str = ""  # the client of the token: "car", "web", "cli", "mcp" or "other", "" when not known


@dataclass(frozen=True)
class ServerTool:
    name: str
    description: str
    parameters: dict
    handler: Callable[[dict, UserContext], Awaitable[str]]
    required_role: str | None = None
    # May run alongside other tools of the same model response. Reads may; a tool that changes
    # something waits for them and runs alone.
    concurrent: bool = True
    # The clients it may be called from, None for all, and what the model is told from another one.
    clients: frozenset[str] | None = None
    client_refusal: str = ""
    client_refusals: Mapping[str, str] = field(default_factory=dict)  # another text, for a client by name
    # Its arguments and output are in no event and no log, whether the turn is private or not.
    private: bool = False
    # What the user is told while this runs: a stable code and a default text.
    status: tuple[str, str] = WORKING

    def __post_init__(self) -> None:
        if self.clients is not None and not self.client_refusal.strip():
            raise ValueError(f"The tool '{self.name}' names clients and has no client_refusal")


@dataclass(frozen=True)
class ClientTool:
    name: str
    description: str
    parameters: dict


def _definition(name: str, description: str, parameters: dict) -> dict:
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": parameters,
        "strict": False,
    }


def _parse_arguments(
    name: str, schema: dict, raw_arguments: str, private: bool = False
) -> tuple[dict, str | None]:
    """The arguments as a dict, or an error message for the model. With `private` the message names
    the field and the rule of the schema, and never what the model sent."""
    try:
        arguments = json.loads(raw_arguments or "{}")
    except ValueError as exc:
        reason = "" if private else f" ({exc})"
        return {}, f"Error: arguments for '{name}' are not valid JSON{reason}."
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(arguments),
        key=lambda e: list(e.path),
    )
    if errors:
        details = "; ".join(
            f"{'.'.join(str(p) for p in e.path) or 'arguments'}: "
            + (f"{e.validator} {e.validator_value}" if private else e.message)
            for e in errors
        )
        return {}, f"Error: invalid arguments for '{name}': {details}"
    return arguments, None


class ToolSet:
    def __init__(self, server: list[ServerTool], client: list[ClientTool]):
        self._server = {t.name: t for t in server}
        self._client = {t.name: t for t in client}

    def definitions(self) -> list[dict]:
        # Every server tool is listed whatever the role: execute() enforces roles, so a
        # denied call is visible as an authorization_denied event (and prompt agents
        # cannot hide tools anyway).
        server = [_definition(t.name, t.description, t.parameters) for t in self._server.values()]
        client = [_definition(t.name, t.description, t.parameters) for t in self._client.values()]
        return server + client

    def is_client(self, name: str) -> bool:
        return name in self._client

    def status_of(self, name: str) -> tuple[str, str]:
        tool = self._server.get(name)
        return tool.status if tool is not None else WORKING

    def is_concurrent(self, name: str) -> bool:
        tool = self._server.get(name)
        return tool is None or tool.concurrent

    def is_private(self, name: str) -> bool:
        """An unknown name counts as private: it may be a private tool by the wrong name."""
        tool = self._server.get(name)
        return tool.private if tool is not None else name not in self._client

    def validate_client(self, name: str, raw_arguments: str) -> str | None:
        """The error to give the model when a client tool call has invalid arguments, else None.
        The client declared the schema, but the model is not bound to it."""
        _, error = _parse_arguments(name, self._client[name].parameters, raw_arguments)
        return error

    async def execute(self, name: str, raw_arguments: str, user: UserContext, emit: Emit) -> str:
        tool = self._server.get(name)
        if tool is None:
            return f"Error: unknown tool '{name}'."
        if tool.clients is not None and user.client not in tool.clients:
            logger.warning("client not allowed: tool %s, client '%s'", tool.name, user.client)
            await emit(
                trace(
                    "client_not_allowed",
                    "tools",
                    f"{tool.name} is not for the client '{user.client}'",
                    tool=tool.name,
                    client=user.client,
                )
            )
            return tool.client_refusals.get(user.client, tool.client_refusal)
        if tool.required_role is not None and tool.required_role not in user.roles:
            logger.warning(
                "authorization denied: tool %s requires role %s (user %s)",
                tool.name,
                tool.required_role,
                user.oid,
            )
            await emit(
                trace(
                    "authorization_denied",
                    "tools",
                    f"{tool.name} requires role {tool.required_role}",
                    tool=tool.name,
                    required_role=tool.required_role,
                )
            )
            return (
                f"Refused: the signed-in user is not allowed to use '{tool.name}' "
                f"(requires role {tool.required_role})."
            )
        arguments, error = _parse_arguments(name, tool.parameters, raw_arguments, tool.private)
        if error is not None:
            return error
        token = current_emit.set(emit)
        private = current_private.set(current_private.get() or tool.private)
        try:
            return await tool.handler(arguments, user)
        except Exception:  # tool failures go back to the model, not up the stack
            log_exception(logger, "Tool %s failed", name)
            return f"Error: the tool '{name}' failed. Tell the user it is unavailable right now."
        finally:
            current_private.reset(private)
            current_emit.reset(token)
