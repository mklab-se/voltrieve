"""The page's files and its settings."""

from urllib.parse import unquote

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app

POLICY = (
    "default-src 'self'; connect-src 'self' https://login.microsoftonline.com; "
    "frame-src https://login.microsoftonline.com; frame-ancestors 'self'; img-src 'self' data:; "
    "style-src 'self' 'unsafe-inline'"
)
LONG = "public, max-age=31536000, immutable"


@pytest.fixture
def dist(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets" / "deep").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>å</title>", encoding="utf-8")
    for name in ("x.js", "theme.js", "index-B7dK_x-9.js", "index-DiwrgTda.css", "msal-redirect.js", "deep/y.js"):
        (dist / "assets" / name).write_text(f"/* {name} */")
    (dist / "notes.txt").write_text("not for the page")
    (tmp_path / "secret.txt").write_text("secret")
    (dist / "assets" / "link.js").symlink_to(tmp_path / "secret.txt")
    return dist


def client_of(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.fixture
async def client(settings, validator, dist):
    app = create_app(settings.model_copy(update={"site_dir": str(dist), "site_client_id": "site-id"}), validator)
    async with client_of(app) as c:
        yield c


def check_headers(response):
    assert response.headers["Content-Security-Policy"] == POLICY
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert "X-Frame-Options" not in response.headers and "Set-Cookie" not in response.headers


async def test_the_page(client):
    r = await client.get("/")
    assert (r.status_code, r.headers["Content-Type"]) == (200, "text/html; charset=utf-8")
    assert r.headers["Cache-Control"] == "no-cache" and "<title>å" in r.text
    check_headers(r)


@pytest.mark.parametrize(
    "name, kind, cache",
    [
        ("x.js", "text/javascript", "no-cache"),
        ("theme.js", "text/javascript", "no-cache"),
        ("msal-redirect.js", "text/javascript", "no-cache"),  # eight letters, and no hash
        ("index-B7dK_x-9.js", "text/javascript", LONG),
        ("index-DiwrgTda.css", "text/css", LONG),
        ("deep/y.js", "text/javascript", "no-cache"),
    ],
)
async def test_an_asset(client, name, kind, cache):
    r = await client.get(f"/assets/{name}")
    assert r.status_code == 200 and r.text == f"/* {name} */"
    assert r.headers["Content-Type"].startswith(kind) and r.headers["Cache-Control"] == cache
    check_headers(r)


@pytest.mark.parametrize(
    "path",
    [
        "/assets/../index.html", "/assets/..%2findex.html", "/assets/%2e%2e/%2e%2e/secret.txt", "/assets/..%5cx.js",
        "/assets/../../secret.txt", "/assets//etc/passwd", "/assets/%2Fetc/passwd", "/assets/link.js", "/assets/deep",
        "/assets/", "/assets", "/assets/nope.js", "/assets/x.js%00", "/assets/./x.js", "/index.html", "/notes.txt",
        "/orders", "/some/page", "/staff", "/favicon.ico", "/..", "/../secret.txt",
    ],
)  # fmt: skip
async def test_nothing_else_is_served(settings, validator, dist, path):
    app = create_app(settings.model_copy(update={"site_dir": str(dist)}), validator)
    status, body = await raw_get(app, path)
    assert status == (405 if path == "/orders" else 404)  # that one is a machine route, with POST
    assert not any(text in body for text in (b"secret", b"<title>", b"/*"))


async def raw_get(app, path: str) -> tuple[int, bytes]:
    """With the path as it is: a client would resolve the dots."""
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "scheme": "http",
        "path": unquote(path), "raw_path": path.encode(), "query_string": b"", "root_path": "", "headers": [],
        "client": ("c", 1), "server": ("t", 80),
    }  # fmt: skip
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    return sent[0]["status"], b"".join(message.get("body", b"") for message in sent)


async def test_the_raw_request_gets_a_file(settings, validator, dist):
    """So the test above is not empty."""
    app = create_app(settings.model_copy(update={"site_dir": str(dist)}), validator)
    assert await raw_get(app, "/assets/x.js") == (200, b"/* x.js */")


async def test_the_settings(client, settings):
    r = await client.get("/config.json")
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-cache"
    assert r.json() == {
        "tenantId": settings.tenant_id,
        "clientId": "site-id",
        "scope": f"api://{settings.dealer_client_id}/manage_as_user",
    }
    check_headers(r)


async def test_without_built_files(settings, validator, auth_header, tmp_path):
    for empty in (tmp_path, tmp_path / "missing"):
        app = create_app(settings.model_copy(update={"site_dir": str(empty)}), validator)
        async with client_of(app) as c:
            assert (await c.get("/")).status_code == 404
            assert (await c.get("/assets/x.js")).status_code == 404
            assert (await c.get("/config.json")).status_code == 200
            assert (await c.get("/healthz")).status_code == 200
            assert (await c.get("/stock", headers=auth_header)).status_code == 200


async def test_the_api_has_its_own_headers(client, auth_header):
    r = await client.get("/stock", headers=auth_header)
    assert "Content-Security-Policy" not in r.headers
