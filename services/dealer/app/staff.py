"""The routes of the representatives: a signed-in person with a role, and a bearer token on each call."""

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.auth import Claims
from app.data import DealerData
from app.hub import Hub
from app.logging_setup import correlation_id
from app.model import AlreadyDelivered, NotFound, Order, OutOfStock
from app.routes import ready_store, validated

KEEPALIVE_SECONDS = 15.0
STREAM_TIME = timedelta(minutes=30)  # then the page comes back with a fresh token
NO_NAME = "Representative"


async def representative(request: Request) -> Claims:
    return await validated(request, request.app.state.staff_validator)


Representative = Annotated[Claims, Depends(representative)]


class StockChange(BaseModel):
    model: str
    city: str
    change: Literal[1, -1]


class Delivery(BaseModel):
    city: str


def order_json(order: Order) -> dict:
    return {
        name: value.isoformat() if isinstance(value, datetime) else value for name, value in vars(order).items()
    }


async def follow(hub: Hub, clock: Callable[[], datetime]) -> AsyncIterator[str]:
    """What happens from now on, until the time is up or the hub dropped a reader that was too slow."""
    end = clock() + STREAM_TIME
    queue = hub.subscribe()
    try:
        yield ": open\n\n"
        while clock() < end and hub.subscribed(queue):
            try:
                name, data = await asyncio.wait_for(queue.get(), KEEPALIVE_SECONDS)
            except TimeoutError:
                yield ": keepalive\n\n"
            else:
                yield f"event: {name}\ndata: {json.dumps(data)}\n\n"
    finally:
        hub.unsubscribe(queue)


def build_staff_router(data: DealerData) -> APIRouter:
    router = APIRouter(prefix="/staff", dependencies=[Depends(representative)])

    def done(request: Request, kind: str, claims: Claims, **values: str) -> None:
        hub = request.app.state.hub
        hub.add(kind, claims.name or NO_NAME, correlation_id=correlation_id.get(), **values)
        hub.changed()

    def check(model: str | None, city: str) -> None:
        try:
            data.check(model, city)
            return
        except ValueError:
            pass
        raise HTTPException(422, "Unknown model or city.")

    @router.get("/state")
    async def state(request: Request) -> dict:
        store = await ready_store(request)
        await store.purge(request.app.state.clock())
        rows = data.stock_rows(await store.stock(), None, None)
        stock: dict[str, dict[str, int]] = {}
        for row in rows:
            stock.setdefault(row.model, {})[row.city] = row.in_stock
        return {
            "models": [
                {"slug": slug, "name": slug.capitalize(), "price": data.price_sek(slug)} for slug in data.models
            ],
            "cities": data.cities,
            "stock": stock,
            "orders": [order_json(order) for order in await store.orders()],
            "log": request.app.state.hub.latest(),
        }

    @router.post("/stock")
    async def change_stock(request: Request, body: StockChange, claims: Representative) -> dict:
        check(body.model, body.city)
        store = await ready_store(request)
        try:
            left = await store.change_stock(body.model, body.city, body.change)
        except OutOfStock:
            raise HTTPException(409, f"There is none in stock in {body.city}.") from None
        except NotFound:
            raise HTTPException(404, "Unknown stock.") from None
        done(request, "stock_changed", claims, model=body.model, city=body.city, result=str(left))
        return {"in_stock": left}

    @router.post("/orders/{number}/deliver")
    async def deliver(request: Request, number: str, body: Delivery, claims: Representative) -> dict:
        check(None, body.city)
        store = await ready_store(request)
        by = claims.name or NO_NAME
        try:
            order = await store.deliver(number, body.city, by, request.app.state.clock())
        except AlreadyDelivered:
            raise HTTPException(409, "This order is already delivered.") from None
        except OutOfStock:
            raise HTTPException(409, f"There is none in stock in {body.city}.") from None
        except NotFound:
            raise HTTPException(404, "Unknown order.") from None
        done(request, "delivered", claims, model=order.model, city=body.city, number=order.number)
        return order_json(order)

    @router.delete("/orders/{number}", status_code=204)
    async def delete(request: Request, number: str, claims: Representative) -> None:
        store = await ready_store(request)
        try:
            order = await store.delete(number)
        except NotFound:
            raise HTTPException(404, "Unknown order.") from None
        done(request, "deleted", claims, model=order.model, number=order.number)

    @router.post("/reset", status_code=204)
    async def reset(request: Request, claims: Representative) -> None:
        store = await ready_store(request)
        await store.reset(data.opening_stock)
        request.app.state.hub.clear()
        done(request, "reset", claims)

    @router.get("/events")
    async def events(request: Request) -> StreamingResponse:
        state = request.app.state
        return StreamingResponse(
            follow(state.hub, state.clock), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"}
        )

    return router
