"""Who is calling a door: the check of an Entra ID v2 token issued for that door and no other."""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx
import jwt

logger = logging.getLogger(__name__)

NAME_LENGTH = 100


class AuthError(Exception):
    """`error` is the OAuth error code for the WWW-Authenticate header."""

    def __init__(self, status: int, detail: str, error: str = "invalid_token"):
        super().__init__(detail)
        self.status, self.detail, self.error = status, detail, error


@dataclass(frozen=True)
class Caller:
    oid: str
    app: str  # the name of the calling app, from our own list
    roles: frozenset[str]
    scopes: frozenset[str]
    application: bool = False  # no person behind the call
    name: str = field(default="", repr=False)


class DoorTokenValidator:
    """One per door. A person's token needs a scope of the door, an application's has none and needs a role."""

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        identifier: str,
        scopes: frozenset[str],
        apps: dict[str, str],
        *,
        applications: bool = False,
        jwks_fetcher: Callable[[], dict] | None = None,
        miss_refetch_interval: float = 300.0,
    ):
        if not tenant_id or not client_id:
            raise ValueError("a door needs a tenant and a client id")
        self._scopes, self._apps, self._applications = scopes, apps, applications
        self._tenant = tenant_id
        self._issuer = f"https://login.microsoftonline.com/{tenant_id}/v2.0"
        self._jwks_url = f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
        self._audiences = [client_id, identifier]
        self._fetcher = jwks_fetcher or self._fetch_jwks
        self._keys: dict[str, jwt.PyJWK] = {}
        self._lock = asyncio.Lock()
        self._interval = miss_refetch_interval
        self._missed: float | None = None

    def _fetch_jwks(self) -> dict:
        response = httpx.get(self._jwks_url, timeout=10.0)
        response.raise_for_status()
        return response.json()

    async def _load(self) -> None:
        try:
            jwks = await asyncio.to_thread(self._fetcher)
            keys = jwt.PyJWKSet.from_dict({"keys": [k for k in jwks.get("keys", []) if k.get("kty") == "RSA"]})
        except Exception as exc:
            logger.warning("Failed to load signing keys: %s", type(exc).__name__)
            raise AuthError(503, "Unable to load signing keys") from None
        self._keys = {key.key_id: key for key in keys.keys if key.key_id}

    def _cooling(self) -> bool:
        return self._missed is not None and time.monotonic() - self._missed < self._interval

    async def _key(self, kid: str) -> jwt.PyJWK:
        if kid in self._keys:
            return self._keys[kid]
        async with self._lock:
            if kid not in self._keys:
                if self._keys:  # a forged kid must not make us fetch again and again
                    if self._cooling():
                        raise AuthError(401, "Unknown signing key")
                    self._missed = time.monotonic()
                await self._load()
        if kid not in self._keys:
            raise AuthError(401, "Unknown signing key")
        return self._keys[kid]

    async def validate(self, authorization: str | None) -> Caller:
        parts = (authorization or "").split(" ")
        if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
            raise AuthError(401, "Missing bearer token")
        try:
            kid = jwt.get_unverified_header(parts[1]).get("kid")
        except jwt.PyJWTError:
            raise AuthError(401, "Invalid token") from None
        if not isinstance(kid, str) or not kid:
            raise AuthError(401, "Invalid token")
        key = await self._key(kid)
        try:
            payload = jwt.decode(
                parts[1],
                key.key,
                algorithms=["RS256"],
                audience=self._audiences,
                issuer=self._issuer,
                options={"require": ["exp", "iss", "aud", "nbf"]},
            )
        except jwt.PyJWTError:
            raise AuthError(401, "Invalid token") from None
        return self._caller(payload)

    def _caller(self, payload: dict) -> Caller:
        oid = payload.get("oid")
        if not isinstance(oid, str) or not oid or payload.get("tid", self._tenant) != self._tenant:
            raise AuthError(401, "Invalid token")
        application = "scp" not in payload
        scopes = frozenset() if application else frozenset(str(payload["scp"]).split()) & self._scopes
        if application and not self._applications:
            raise AuthError(403, "Wrong kind of caller", "insufficient_scope")
        if not application and not scopes:
            raise AuthError(403, "Missing required scope", "insufficient_scope")
        app = self._apps.get(str(payload.get("azp") or ""))
        if app is None and not application:  # an application is known by its role instead
            raise AuthError(403, "This app may not use the door", "access_denied")
        roles = payload.get("roles")
        roles = frozenset(str(r) for r in roles) if isinstance(roles, list) else frozenset()
        if not roles:
            raise AuthError(403, "Missing required role", "access_denied")
        return Caller(oid, app or "application", roles, scopes, application, _name(payload))


def _name(payload: dict) -> str:
    for text in (payload.get("name"), payload.get("preferred_username")):
        if isinstance(text, str):
            line = " ".join("".join(c if c.isprintable() else " " for c in text).split())[:NAME_LENGTH]
            if line:
                return line
    return ""
