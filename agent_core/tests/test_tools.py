import json
import logging
from dataclasses import replace

import pytest

from voltrieve_agent.private import current_private, log_exception
from voltrieve_agent.tools import ClientTool, ServerTool, ToolSet, UserContext

SCHEMA = {
    "type": "object",
    "properties": {"city": {"type": "string"}},
    "required": ["city"],
    "additionalProperties": False,
}


def make_tool(calls, role=None):
    async def handler(args, user):
        calls.append((args, user))
        return f"ok {args['city']}"

    return ServerTool("lookup", "Look up", SCHEMA, handler, required_role=role)


async def test_executes_with_valid_arguments(user, events):
    calls = []
    toolset = ToolSet([make_tool(calls)], [])
    out = await toolset.execute("lookup", json.dumps({"city": "Oslo"}), user, events)
    assert out == "ok Oslo"
    assert calls == [({"city": "Oslo"}, user)]


async def test_invalid_arguments_skip_handler(user, events):
    calls = []
    toolset = ToolSet([make_tool(calls)], [])
    out = await toolset.execute("lookup", json.dumps({"town": "Oslo"}), user, events)
    assert out.startswith("Error: invalid arguments")
    assert calls == []


async def test_malformed_json_is_an_error_output(user, events):
    toolset = ToolSet([make_tool([])], [])
    out = await toolset.execute("lookup", "{nope", user, events)
    assert "not valid JSON" in out


async def test_handler_exception_becomes_output(user, events):
    async def boom(args, user):
        raise RuntimeError("down")

    toolset = ToolSet([ServerTool("lookup", "d", SCHEMA, boom)], [])
    out = await toolset.execute("lookup", '{"city": "x"}', user, events)
    assert "failed" in out and "RuntimeError" not in out and "down" not in out


async def test_role_denied_refuses_and_traces(user, events):
    calls = []
    toolset = ToolSet([make_tool(calls, role="Admin")], [])
    out = await toolset.execute("lookup", '{"city": "x"}', user, events)
    assert out.startswith("Refused")
    assert calls == []
    assert [e.data["step"] for e in events.events] == ["authorization_denied"]


async def test_date_format_is_enforced(user, events):
    calls = []

    async def handler(args, u):
        calls.append(args)
        return "ok"

    schema = {"type": "object", "properties": {"d": {"type": "string", "format": "date"}}}
    toolset = ToolSet([ServerTool("t", "d", schema, handler)], [])
    out = await toolset.execute("t", '{"d": "tomorrow"}', user, events)
    assert out.startswith("Error: invalid arguments") and calls == []


async def test_unknown_tool(user, events):
    out = await ToolSet([], []).execute("nope", "{}", user, events)
    assert "unknown tool" in out


def test_definitions_list_every_server_tool_whatever_the_role(user):
    toolset = ToolSet(
        [make_tool([]), ServerTool("book", "d", SCHEMA, None, required_role="Admin")],
        [ClientTool("pick_file", "Pick", {"type": "object", "properties": {}})],
    )
    assert [d["name"] for d in toolset.definitions()] == ["lookup", "book", "pick_file"]
    assert all(d["type"] == "function" for d in toolset.definitions())
    assert toolset.is_client("pick_file") and not toolset.is_client("lookup")


def test_client_arguments_are_validated_against_the_clients_schema():
    schema = {
        "type": "object",
        "properties": {"times": {"type": "integer", "minimum": 1, "maximum": 10}},
        "required": ["times"],
    }
    toolset = ToolSet([], [ClientTool("make_noise", "Ring", schema)])
    assert toolset.validate_client("make_noise", '{"times": 3}') is None
    assert toolset.validate_client("make_noise", "{bad") .startswith("Error: arguments for 'make_noise' are not valid JSON")
    for bad in ('{}', '{"times": 99}', '{"times": "x"}', '[]'):
        assert toolset.validate_client("make_noise", bad).startswith("Error: invalid arguments for 'make_noise'")
    assert "times" in toolset.validate_client("make_noise", '{"times": 99}')


async def test_role_denied_is_logged_as_warning(user, events, caplog):
    toolset = ToolSet([make_tool([], role="Admin")], [])
    with caplog.at_level("WARNING", logger="voltrieve_agent.tools"):
        await toolset.execute("lookup", '{"city": "x"}', user, events)
    record = next(r for r in caplog.records if "authorization denied" in r.getMessage())
    assert record.levelname == "WARNING"
    assert "lookup" in record.getMessage() and "Admin" in record.getMessage() and user.oid in record.getMessage()


REFUSAL = "Not from here."
SECRET = "SECRET STREET 1"


def client_user(client, *roles):
    return UserContext("oid-1", "anna@example.com", frozenset(roles), "Anna A", client)


def web_tool(calls, **extra):
    tool = make_tool(calls, role="Admin")
    return replace(tool, clients=frozenset({"web", "cli"}), client_refusal=REFUSAL, **extra)


@pytest.mark.parametrize("client", ["car", "other", "test", "", "WEB", "web "])
@pytest.mark.parametrize("roles", [(), ("Admin",)])
async def test_a_client_a_tool_is_not_for_is_refused_before_the_role(events, caplog, client, roles):
    calls = []
    arguments = json.dumps({"city": SECRET})
    with caplog.at_level(logging.DEBUG):
        out = await ToolSet([web_tool(calls)], []).execute("lookup", arguments, client_user(client, *roles), events)
    assert out == REFUSAL and calls == []
    (event,) = events.events
    assert event.data == {
        "step": "client_not_allowed", "component": "tools",
        "detail": f"lookup is not for the client '{client}'", "tool": "lookup", "client": client,
    }
    (record,) = caplog.records
    assert record.levelname == "WARNING" and "lookup" in record.getMessage()
    assert SECRET not in str(record.__dict__) and "Anna" not in str(record.__dict__)


async def test_a_refused_client_is_refused_before_the_arguments_are_read(events):
    out = await ToolSet([web_tool([])], []).execute("lookup", "{nope", client_user("car"), events)
    assert out == REFUSAL


@pytest.mark.parametrize("client", ["web", "cli"])
async def test_a_client_a_tool_is_for_still_needs_the_role(events, client):
    calls = []
    toolset = ToolSet([web_tool(calls)], [])
    out = await toolset.execute("lookup", '{"city": "x"}', client_user(client), events)
    assert out.startswith("Refused") and calls == []
    out = await toolset.execute("lookup", '{"city": "x"}', client_user(client, "Admin"), events)
    assert out == "ok x"


async def test_a_tool_for_no_client_refuses_all(events):
    tool = replace(web_tool([]), clients=frozenset())
    assert await ToolSet([tool], []).execute("lookup", "{}", client_user("web", "Admin"), events) == REFUSAL


async def test_a_tool_that_names_no_clients_runs_for_any(user, events):
    assert user.client == "" and user.name == ""
    assert await ToolSet([make_tool([])], []).execute("lookup", '{"city": "x"}', user, events) == "ok x"


def test_the_name_of_a_user_is_not_in_its_repr():
    assert "Anna A" not in repr(client_user("web"))


PRIVATE_SCHEMA = {
    "type": "object",
    "properties": {
        "address": {"type": "string", "minLength": 5, "maxLength": 20},
        "model": {"type": "string", "enum": ["fjord"]},
    },
    "required": ["model"],
    "additionalProperties": False,
}


@pytest.mark.parametrize(
    ("arguments", "named"),
    [
        ({"model": "fjord", "address": SECRET * 2}, "address: maxLength 20"),
        ({"model": "fjord", "address": "SEC"}, "address: minLength 5"),
        ({"model": "fjord", "address": [SECRET]}, "address: type string"),
        ({"model": SECRET}, "model: enum ['fjord']"),
        ({"model": "fjord", SECRET: "x"}, "arguments: additionalProperties False"),
        ({"address": SECRET}, "arguments: required ['model']"),
        (SECRET, "arguments: type object"),
    ],
)
async def test_the_validation_error_of_a_private_tool_names_the_field_and_not_the_value(
    user, events, arguments, named
):
    tool = ServerTool("order", "d", PRIVATE_SCHEMA, None, private=True)
    out = await ToolSet([tool], []).execute("order", json.dumps(arguments), user, events)
    assert out == f"Error: invalid arguments for 'order': {named}"


async def test_malformed_json_for_a_private_tool_quotes_nothing(user, events):
    tool = ServerTool("order", "d", PRIVATE_SCHEMA, None, private=True)
    out = await ToolSet([tool], []).execute("order", '{"address": "\\S"}', user, events)
    assert out == "Error: arguments for 'order' are not valid JSON."


async def test_a_failing_private_handler_is_logged_without_a_text(user, events, caplog):
    async def handler(args, u):
        try:
            raise ValueError(SECRET)
        except ValueError:
            log_exception(logging.getLogger("inside"), "A helper failed")
            raise RuntimeError(args["city"])

    tool = ServerTool("order", "d", SCHEMA, handler, private=True)
    with caplog.at_level(logging.DEBUG):
        out = await ToolSet([tool], []).execute("order", json.dumps({"city": SECRET}), user, events)
    assert out == "Error: the tool 'order' failed. Tell the user it is unavailable right now."
    assert "Tool order failed: RuntimeError" in caplog.text and "A helper failed: ValueError" in caplog.text
    assert SECRET not in str([r.__dict__ for r in caplog.records]) and SECRET not in caplog.text
    assert not [r for r in caplog.records if r.exc_info] and current_private.get() is False


async def test_a_failing_ordinary_tool_is_logged_as_before(user, events, caplog):
    async def handler(args, u):
        raise RuntimeError("down")

    await ToolSet([ServerTool("lookup", "d", SCHEMA, handler)], []).execute("lookup", '{"city": "x"}', user, events)
    assert caplog.records[-1].exc_info


@pytest.mark.parametrize("refusal", ["", "  "])
def test_a_tool_that_names_clients_must_have_a_refusal(refusal):
    with pytest.raises(ValueError, match="order.*client_refusal"):
        ServerTool("order", "d", SCHEMA, None, clients=frozenset({"web"}), client_refusal=refusal)
    ServerTool("order", "d", SCHEMA, None)
