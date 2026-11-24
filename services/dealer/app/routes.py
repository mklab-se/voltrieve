import datetime as dt
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import AfterValidator, BaseModel, Field, StringConstraints, field_validator

from app.auth import AuthError, Claims, TokenValidator
from app.data import DealerData, UnknownArticle, UnknownCity, UnknownModel, today
from app.logging_setup import correlation_id
from app.model import (
    NotFound,
    Offer,
    OfferExpired,
    Order,
    SameTurn,
    clean_address,
    new_offer_id,
    one_line,
)
from app.store import Store

DATA_PATH = Path(__file__).parent / "data.json"
PARTS_STOCK_PATH = Path(__file__).parent / "parts-stock.json"  # written by the generator

RUNA = "Runa"  # the one caller of the machine routes
CURRENCY = "SEK"
THANKS = "Thank you for your order. Voltrieve will be in touch about delivery."
# Why there is no order: the status, what Runa is told, and what the log says.
REFUSALS = {
    SameTurn: (
        409,
        "The customer has not answered yet. Read back the car, the price and the address, and ask.",
        "The customer has not answered yet",
    ),
    OfferExpired: (410, "The offer has expired. Make a new one.", "The offer has expired"),
    NotFound: (404, "Unknown offer.", "Unknown offer"),
}

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


async def validated(request: Request, validator: TokenValidator) -> Claims:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(401, "Missing bearer token")
    try:
        return await validator.validate(header)
    except AuthError as exc:
        if exc.status_code == 403:  # signed in. Anyone on the internet can get a 401.
            request.app.state.hub.add("refused", None, correlation_id=correlation_id.get())
        raise HTTPException(exc.status_code, exc.detail) from exc


async def current_claims(request: Request) -> Claims:
    return await validated(request, request.app.state.validator)


async def ready_store(request: Request) -> Store:
    await request.app.state.keeper.ready()
    return request.app.state.store


def note(request: Request, kind: str, **values: str | None) -> None:
    request.app.state.hub.add(kind, RUNA, correlation_id=correlation_id.get(), **values)


class TestDriveRequest(BaseModel):
    __test__ = False

    model: str
    city: str
    date: dt.date
    requester: str

    @field_validator("date")
    @classmethod
    def date_in_future(cls, value: dt.date) -> dt.date:
        if value <= today():
            raise ValueError("date must be in the future")
        return value


class OfferRequest(BaseModel):
    model: str
    address: Annotated[str, AfterValidator(clean_address)]
    customer: Text
    name: Annotated[str, Field(max_length=200), AfterValidator(one_line)]
    turn: Text
    client: Literal["web", "cli"]


class OrderRequest(BaseModel):
    offer: Text
    customer: Text
    turn: Text


def confirmation(order: Order) -> dict:
    return {
        "order_number": order.number,
        "model": order.model,
        "price": order.price_sek,
        "currency": CURRENCY,
        "name": order.name,
        "address": order.address,
        "ordered": order.created.isoformat(),
        "message": THANKS,
    }


def build_router(data: DealerData) -> APIRouter:
    router = APIRouter()

    @router.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    protected = APIRouter(dependencies=[Depends(current_claims)])

    @protected.get("/stock")
    async def stock(request: Request, model: str | None = None, city: str | None = None) -> dict:
        try:
            data.check(model, city)
        except ValueError as exc:
            raise HTTPException(422, f"Unknown value: {exc}") from exc
        store = await ready_store(request)
        rows = data.stock_rows(await store.stock(), model, city)
        note(request, "stock", model=model, city=city, result=str(sum(r.in_stock for r in rows)))
        return {"rows": [r.__dict__ for r in rows]}

    @protected.get("/parts/stock")
    async def part_stock(request: Request, article: str, city: str | None = None) -> dict:
        try:
            rows = data.part_stock(article, city)
        except UnknownArticle as exc:
            raise HTTPException(404, f"Unknown article: {exc}, not a Voltrieve article number") from exc
        except UnknownCity as exc:
            raise HTTPException(422, f"Unknown city: {exc}. The dealers are in {', '.join(data.cities)}") from exc
        counted = rows[:1] if city else rows
        note(request, "part_stock", city=city, result=str(sum(r.in_stock for r in counted)))
        return {"article": article, "rows": [r.__dict__ for r in rows]}

    @protected.get("/prices")
    async def prices(request: Request, market: Literal["SE", "EU"] = "SE") -> dict:
        p = data.prices(market)
        note(request, "prices")
        return {"market": p.market, "currency": p.currency, "prices": p.prices}

    @protected.post("/test-drives", status_code=201)
    async def book(request: Request, body: TestDriveRequest) -> dict:
        try:
            b = data.book(body.model, body.city, body.date, body.requester)
        except ValueError as exc:
            raise HTTPException(422, f"Invalid booking: {exc}") from exc
        note(request, "test_drive", model=b.model, city=b.city)
        return {
            "booking_id": b.booking_id,
            "model": b.model,
            "city": b.city,
            "date": b.date.isoformat(),
            "requester": b.requester,
        }

    def price_of(model: str) -> int:
        try:
            return data.price_sek(model)
        except UnknownModel:
            pass
        raise HTTPException(422, "Unknown model.")

    @protected.get("/orders/terms")
    async def terms(request: Request, model: str) -> dict:
        price = price_of(model)
        store = await ready_store(request)
        rows = data.stock_rows(await store.stock(), model, None)
        note(request, "terms", model=model)
        return {
            "model": model,
            "price": price,
            "currency": CURRENCY,
            "stock": {r.city: r.in_stock for r in rows},
        }

    @protected.post("/orders/offers", status_code=201)
    async def make_offer(request: Request, body: OfferRequest) -> dict:
        offer = Offer(
            id=new_offer_id(),
            price_sek=price_of(body.model),
            created=request.app.state.clock(),
            order=None,
            **body.model_dump(),
        )
        store = await ready_store(request)
        await request.app.state.keeper.purge()
        await store.save_offer(offer)
        note(request, "offer", model=offer.model, client=offer.client)
        return {
            "offer": offer.id,
            "model": offer.model,
            "price": offer.price_sek,
            "currency": CURRENCY,
            "name": offer.name,
            "address": offer.address,
        }

    @protected.post("/orders", status_code=201)
    async def place(request: Request, response: Response, body: OrderRequest) -> dict:
        store = await ready_store(request)
        refusal = None
        try:
            order, new = await store.place(body.offer, body.customer, body.turn, request.app.state.clock())
        except tuple(REFUSALS) as exc:
            refusal = REFUSALS[type(exc)]
        if refusal:
            status, detail, result = refusal
            note(request, "order", result=result)
            raise HTTPException(status, detail)
        result = None if new else "Already placed"
        note(request, "order", model=order.model, client=order.client, number=order.number, result=result)
        request.app.state.hub.changed()  # also a repeated one: the first may have been lost on its way
        if not new:
            response.status_code = 200
        return confirmation(order)

    router.include_router(protected)
    return router
