"""The page of the representatives: its built files and its settings. Anyone may read them."""

import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import JSONResponse

from app.settings import Settings

ENTRA = "https://login.microsoftonline.com"
POLICY = (
    f"default-src 'self'; connect-src 'self' {ENTRA}; frame-src 'self' {ENTRA}; frame-ancestors 'self'; "
    "img-src 'self' data:; style-src 'self' 'unsafe-inline'"
)
HEADERS = {
    "Content-Security-Policy": POLICY,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-cache",
}
FOREVER = {**HEADERS, "Cache-Control": "public, max-age=31536000, immutable"}
# The build puts a hash in a name, as in index-B7dK_x-9.js. A word is not a hash.
HASHED = re.compile(r".+-(?=[a-z_-]*[A-Z0-9])[A-Za-z0-9_-]{8}\.(js|css)")
TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".woff2": "font/woff2",
}


def read_files(root: Path) -> dict[str, Path]:
    """The page and its assets by name. Only these names are served, so no name can lead elsewhere."""
    found = [root / "index.html", *(root / "assets").rglob("*")]
    return {path.relative_to(root).as_posix(): path for path in found if path.is_file() and not path.is_symlink()}


def build_site_router(settings: Settings) -> APIRouter:
    router = APIRouter()
    files = read_files(Path(settings.site_dir))

    def serve(name: str) -> Response:
        if name not in files:
            raise HTTPException(404)
        path = files[name]
        kind = TYPES.get(path.suffix, "application/octet-stream")
        return Response(path.read_bytes(), media_type=kind, headers=FOREVER if HASHED.fullmatch(path.name) else HEADERS)

    @router.get("/")
    def page() -> Response:
        return serve("index.html")

    @router.get("/assets/{name:path}")
    def asset(name: str) -> Response:
        return serve(f"assets/{name}")

    @router.get("/config.json")
    def config() -> Response:
        found = {
            "tenantId": settings.tenant_id,
            "clientId": settings.site_client_id,
            "scope": f"api://{settings.dealer_client_id}/manage_as_user",
        }
        return JSONResponse(found, headers=HEADERS)

    return router
