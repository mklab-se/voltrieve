"""What every request goes through: the body limit, the log line, and errors that nobody expected."""

import json

import pytest
from httpx import ASGITransport, AsyncClient

from app.guard import MAX_BODY_BYTES
from app.main import create_app

ROUTES = [
    ("POST", "/staff/stock"), ("POST", "/staff/orders/VO-1001/deliver"), ("POST", "/staff/reset"),
    ("DELETE", "/staff/orders/VO-1001"), ("POST", "/orders"), ("POST", "/orders/offers"), ("POST", "/test-drives"),
]  # fmt: skip
MARKER = "Markörgatan 7"


@pytest.fixture
def make_app(settings, validator, staff_validator):
    """For the tests of the log, which goes to the stdout of when the app was made."""
    return lambda: create_app(settings, validator, staff_validator=staff_validator)


@pytest.fixture
def app(make_app):
    return make_app()


async def send(app, method, path, chunks, headers=()):
    """A request whose body comes in chunks. Tells how many were read."""
    read, sent, left = 0, [], list(chunks)
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "", "client": ("c", 1),
        "server": ("t", 80), "headers": [(b"content-type", b"application/json"), *headers],
    }  # fmt: skip

    async def receive():
        nonlocal read
        read += 1
        return {"type": "http.request", "body": left.pop(0), "more_body": bool(left)}

    async def send_message(message):
        sent.append(message)

    await app(scope, receive, send_message)
    answer = {k.decode().lower(): v.decode() for k, v in sent[0]["headers"]}
    return sent[0]["status"], answer, b"".join(m.get("body", b"") for m in sent), read


CHUNK = bytes(16 * 1024)


@pytest.mark.parametrize("method, path", ROUTES)
async def test_a_large_body_is_not_read_without_a_token(app, method, path):
    assert MAX_BODY_BYTES == 64 * 1024
    for headers in ([], [(b"authorization", b"Bearer x")]):
        status, answer, body, read = await send(app, method, path, [CHUNK] * 1000, headers)
        if path == "/staff/reset" or method == "DELETE":  # no body is read there at all
            assert (status, read) == (401, 0)
            continue
        assert (status, read) == (413, 5)  # 64 KiB, and the chunk that is too much
        assert json.loads(body) == {"detail": "Request body too large"}
        assert "x-correlation-id" in answer
        assert answer.get("cache-control") == ("no-store" if path.startswith("/staff/") else None)


@pytest.mark.parametrize("method, path", [*ROUTES, ("GET", "/"), ("GET", "/healthz"), ("POST", "/nowhere")])
@pytest.mark.parametrize("length", [b"65537", b"314572800", b"9" * 5000])
async def test_a_large_declared_body_is_not_read_at_all(app, method, path, length):
    status, answer, _, read = await send(app, method, path, [b"x"], [(b"content-length", length)])
    assert (status, read) == (413, 0) and "x-correlation-id" in answer


async def test_a_body_at_the_limit_is_read(app, auth_header):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t", headers=auth_header) as c:
        exact = b'"' + b"x" * (MAX_BODY_BYTES - 2) + b'"'
        r = await c.post("/orders", content=exact, headers={"Content-Type": "application/json"})
        assert r.status_code == 422
        r = await c.post("/orders", content=exact + b" ", headers={"Content-Type": "application/json"})
        assert r.status_code == 413


@pytest.mark.parametrize("length", ["²".encode("latin-1"), b"-1", b"", b"abc"])
async def test_a_strange_length_is_no_error_of_ours(app, length):
    status, *_ = await send(app, "POST", "/orders", [b"{}"], [(b"content-length", length)])
    assert status == 401


async def test_a_lie_about_the_length_is_still_counted(app):
    status, _, _, read = await send(app, "POST", "/orders", [CHUNK] * 1000, [(b"content-length", b"2")])
    assert (status, read) == (413, 5)


def access_lines(capsys):
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    return [line for line in lines if line["message"] == "request"]


@pytest.mark.parametrize(
    "path, logged",
    [
        ("/a" * 50_000, "/a" * 100),
        ("/a\nb\x00\x1b[31m\u2028\u202ec", "/a b [31m c"),
        ("/stock", "/stock"),
    ],
)
async def test_the_path_in_the_log(make_app, capsys, path, logged):
    app = make_app()
    await send(app, "GET", path, [b""])
    (line,) = access_lines(capsys)
    assert line["path"] == logged


async def test_a_refused_body_is_logged_once(make_app, capsys):
    app = make_app()
    await send(app, "POST", "/orders", [b""], [(b"content-length", b"99999")])
    (line,) = access_lines(capsys)
    assert (line["status"], line["path"]) == (413, "/orders")


async def test_an_error_nobody_expected_is_500_and_shows_nothing(make_app, capsys):
    app = make_app()
    @app.get("/broken")
    def broken():
        raise RuntimeError(MARKER)

    status, answer, body, _ = await send(app, "GET", "/broken", [b""])
    assert (status, json.loads(body)) == (500, {"detail": "Internal error"})
    out = capsys.readouterr()
    assert MARKER not in out.out + out.err + json.dumps(answer)
    assert "Unhandled error: RuntimeError at test_guard.py:" in out.out
    assert '"status": 500' in out.out


async def test_an_error_in_the_middle_of_an_answer_shows_nothing(make_app, capsys):
    app = make_app()
    from fastapi.responses import StreamingResponse

    @app.get("/half")
    def half():
        async def parts():
            yield "one"
            raise RuntimeError(MARKER)

        return StreamingResponse(parts())

    with pytest.raises(RuntimeError) as raised:
        await send(app, "GET", "/half", [b""] * 3)
    assert str(raised.value) == "the answer was cut off"
    assert raised.value.__context__ is None and raised.value.__cause__ is None
    assert MARKER not in capsys.readouterr().out
