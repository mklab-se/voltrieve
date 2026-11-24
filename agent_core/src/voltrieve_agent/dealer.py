"""Client for the dealer service. Every method returns text the model can read."""

import uuid
from collections.abc import Awaitable, Callable

import httpx

from voltrieve_agent.tools import current_correlation_id

# What the model is told to do next, after the terms and after an offer.
ASK_FOR_ADDRESS = (
    "Nothing is ordered yet. A car that no city has in stock can be ordered too, and is delivered when "
    "there is one. Ask the user for the delivery address, and say that it is saved with the order."
)
READ_BACK = "Nothing is ordered yet. Read it back and ask. Wait for the answer in the next message."
# An offer becomes one order, so placing it twice is safe. Starting over is not.
PLACE_AGAIN = (
    "The order may have been received. Call place_car_order once more with the same offer. If that "
    "fails too, tell the user to ask again in a moment, and never say that the car is ordered."
)


class DealerClient:
    def __init__(
        self,
        base_url: str,
        token_provider: Callable[[], Awaitable[str]],
        http: httpx.AsyncClient,
    ):
        self._base_url = base_url.rstrip("/")
        self._token_provider = token_provider
        self._http = http

    async def stock(self, model: str | None = None, city: str | None = None) -> str:
        params = {k: v for k, v in {"model": model, "city": city}.items() if v}
        return await self._call("GET", "/stock", params=params)

    async def part_stock(self, article: str, city: str | None = None) -> str:
        params = {k: v for k, v in {"article": article, "city": city}.items() if v}
        return await self._call("GET", "/parts/stock", params=params)

    async def prices(self, market: str = "SE") -> str:
        return await self._call("GET", "/prices", params={"market": market})

    async def book(self, model: str, city: str, date: str, requester: str) -> str:
        body = {"model": model, "city": city, "date": date, "requester": requester}
        return await self._call("POST", "/test-drives", json=body)

    async def terms(self, model: str) -> str:
        return await self._call("GET", "/orders/terms", params={"model": model}, then=ASK_FOR_ADDRESS)

    async def offer(
        self, model: str, address: str, customer: str, name: str, turn: str | None, client: str
    ) -> str:
        body = {
            "model": model,
            "address": address,
            "customer": customer,
            "name": name,
            "turn": turn,
            "client": client,
        }
        return await self._call("POST", "/orders/offers", json=body, then=READ_BACK)

    async def place(self, offer: str, customer: str, turn: str | None) -> str:
        body = {"offer": offer, "customer": customer, "turn": turn}
        return await self._call("POST", "/orders", json=body, when_unsure=PLACE_AGAIN)

    async def _call(
        self, method: str, path: str, then: str = "", when_unsure: str = "", **kwargs
    ) -> str:
        """The answer, with `then` after it when the call succeeded. `when_unsure` is the text
        for a call that may have been carried out: every transport error and every 5xx."""
        headers = {
            "Authorization": f"Bearer {await self._token_provider()}",
            "X-Correlation-Id": current_correlation_id.get() or str(uuid.uuid4()),
        }
        try:
            response = await self._http.request(
                method, f"{self._base_url}{path}", headers=headers, **kwargs
            )
        except httpx.HTTPError as exc:
            return when_unsure or f"The dealer service could not be reached ({type(exc).__name__}). Try again later."
        if response.is_success:
            return f"{response.text}\n{then}" if then else response.text
        if response.status_code >= 500:
            return when_unsure or (
                f"The dealer service is unavailable (HTTP {response.status_code}). "
                "Try again later."
            )
        return f"The dealer service rejected the request (HTTP {response.status_code}): {_detail(response)}"


def _detail(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    if isinstance(detail, list):  # FastAPI validation errors
        detail = "; ".join(
            f"{'.'.join(str(p) for p in e.get('loc', [])[1:])}: {e.get('msg')}" for e in detail
        )
    return str(detail or response.text or "no details")
