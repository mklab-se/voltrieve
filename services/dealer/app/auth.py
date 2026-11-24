import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx
import jwt

from app.model import one_line

logger = logging.getLogger(__name__)

NAME_LENGTH = 200


class AuthError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class Claims:
    oid: str
    app_id: str
    roles: list[str]
    name: str = ""  # of a person, for the log and the page
    expires: int = 0  # seconds since 1970


class TokenValidator:
    """Validates Entra ID v2 bearer tokens for callers holding a required app role.

    With a required scope the caller is a signed-in person. Without one it is an application: a token
    that has a scope claim at all is refused."""

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        required_role: str,
        jwks_fetcher: Callable[[], dict] | None = None,
        miss_refetch_interval: float = 300.0,
        required_scope: str | None = None,
    ):
        self._client_id = client_id
        self._required_role = required_role
        self._required_scope = required_scope
        self._issuer = f"https://login.microsoftonline.com/{tenant_id}/v2.0"
        self._jwks_url = f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
        self._audiences = [client_id, f"api://{client_id}"]
        self._fetcher = jwks_fetcher or self._fetch_jwks
        self._keys: dict[str, jwt.PyJWK] = {}
        self._lock = asyncio.Lock()
        self._miss_refetch_interval = miss_refetch_interval
        self._last_miss_refresh: float | None = None

    def _fetch_jwks(self) -> dict:
        response = httpx.get(self._jwks_url, timeout=10.0)
        response.raise_for_status()
        return response.json()

    async def _refresh_keys(self) -> None:
        try:
            jwks = await asyncio.to_thread(self._fetcher)
            keyset = jwt.PyJWKSet.from_dict(jwks)
        except Exception as exc:
            logger.warning("Failed to load signing keys: %s", type(exc).__name__)
            raise AuthError(503, "Unable to load signing keys") from exc
        self._keys = {key.key_id: key for key in keyset.keys if key.key_id}

    async def _signing_key(self, kid: str) -> jwt.PyJWK:
        # Cached keys are served without touching the lock, so a slow JWKS fetch
        # never delays tokens signed with a known key.
        key = self._keys.get(kid)
        if key is not None:
            return key
        if self._keys and self._in_cooldown():
            raise AuthError(401, "Unknown signing key")
        async with self._lock:
            key = self._keys.get(kid)
            if key is not None:
                return key
            if self._keys:
                # A miss with keys already loaded: rate limit forged kids.
                if self._in_cooldown():
                    raise AuthError(401, "Unknown signing key")
                self._last_miss_refresh = time.monotonic()
            await self._refresh_keys()
            key = self._keys.get(kid)
        if key is None:
            raise AuthError(401, "Unknown signing key")
        return key

    def _in_cooldown(self) -> bool:
        return (
            self._last_miss_refresh is not None
            and time.monotonic() - self._last_miss_refresh < self._miss_refetch_interval
        )

    @staticmethod
    def _extract_token(bearer: str | None) -> str:
        if not bearer:
            raise AuthError(401, "Missing bearer token")
        parts = bearer.split(" ")
        if len(parts) == 1:
            # A bare token, as passed by callers that already stripped the scheme.
            return parts[0]
        if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
            raise AuthError(401, "Malformed Authorization header")
        return parts[1]

    async def validate(self, bearer: str) -> Claims:
        """Validate "Bearer <jwt>" (or a bare jwt) and return its claims."""
        token = self._extract_token(bearer)
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise AuthError(401, "Invalid token") from exc
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise AuthError(401, "Invalid token")

        key = await self._signing_key(kid)
        try:
            payload = jwt.decode(
                token,
                key.key,
                algorithms=["RS256"],
                audience=self._audiences,
                issuer=self._issuer,
                options={"require": ["exp", "iss", "aud"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthError(401, "Invalid token") from exc

        roles = payload.get("roles")
        if not isinstance(roles, list) or self._required_role not in roles:
            raise AuthError(403, "Missing required role")
        if not self._is_right_kind(payload):
            raise AuthError(403, "Wrong kind of caller")
        oid = payload.get("oid")
        if not isinstance(oid, str) or not oid:
            raise AuthError(401, "Invalid token")
        app_id = payload.get("azp") or payload.get("appid") or ""
        return Claims(oid, str(app_id), [str(r) for r in roles], _name(payload), int(payload["exp"]))

    def _is_right_kind(self, payload: dict) -> bool:
        if self._required_scope is None:
            return "scp" not in payload
        scopes = payload.get("scp")
        return isinstance(scopes, str) and self._required_scope in scopes.split()


def _name(payload: dict) -> str:
    names = [payload.get("name"), payload.get("preferred_username")]
    lines = [one_line(name) for name in names if isinstance(name, str)]
    return next((line[:NAME_LENGTH] for line in lines if line), "")
