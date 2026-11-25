"""The MCP door of the dealers: the stock and the orders of one city, for its staff and their agents.

The city comes from the setting and never from the caller. No name and no address of a customer is in an answer.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Literal

from fastapi import FastAPI, Request
from mcp.server.elicitation import AcceptedElicitation, ElicitationResult
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.resolve import Elicit, Resolve
from pydantic import BaseModel, BeforeValidator, Field, StrictBool
from voltrieve_doors import Call, Caller, Door, DoorConfig, DoorTokenValidator, DoorTool, Gate, RateLimit, Refused
from voltrieve_doors import caller as calling

from app.data import DealerData, UnknownModel
from app.logging_setup import correlation_id
from app.model import AlreadyDelivered, NotFound, Order, OutOfStock, StoreUnavailable, newest_first
from app.routes import CURRENCY, ready_store
from app.settings import Settings
from app.staff import NO_NAME, StockChange, writing
from app.store import Stock

NAME = "dealer"
SCOPE = "dealer.use"
STAFF, AGENT = "Dealer.Staff", "Dealer.Agent"  # of a person, and of an application
AZURE_CLI_CLIENT_ID = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"
CALLS_AT_ONCE = 4
CALLS_PER_MINUTE = 30
ORDERS_AT_ONCE = 50
DEALER_AGENT = "Dealer agent"

UNAVAILABLE = "The dealer system is not available right now."
NO_CITY = "You have no dealer city. Ask the one who runs the dealer system."
UNKNOWN_MODEL = "Unknown model."
UNKNOWN_ORDER = "Unknown order."
ALREADY_DELIVERED = "This order is already delivered."
CANNOT_ASK = "Your AI cannot ask you for a yes, so nothing was delivered. Deliver the order on the staff page."
NOT_DELIVERED = "The person did not say yes. Nothing was delivered."
NO_YES = "No yes from the person"  # in the log

INSTRUCTIONS = (
    "The stock and the open orders of one Voltrieve dealer, in the city of the caller. "
    "Prices are in SEK. Names and addresses of customers are only on the staff page."
)

Change = Annotated[Literal[1, -1], BeforeValidator(StockChange.whole_number)]
Number = Annotated[str, Field(pattern="^VO-[0-9]{1,9}$", description="Number of an order, such as VO-1001")]


class Yes(BaseModel):
    deliver: StrictBool = Field(description="Deliver this order")


class DealerTool(DoorTool):
    """The role must be the one for the kind of caller."""

    def allows(self, who: Caller) -> bool:
        return (AGENT if who.application else STAFF) in who.roles and super().allows(who)


def door_cities(text: str, data: DealerData) -> dict[str, str]:
    """ "oid=City;oid=City". The errors are for the one who starts the service."""
    cities: dict[str, str] = {}
    for item in filter(None, (part.strip() for part in text.split(";"))):
        oid, _, city = (part.strip() for part in item.partition("="))
        if not oid or oid in cities or city not in data.cities:
            raise ValueError("DEALER_DOOR_CITIES: each oid once, with a city of a dealer")
        cities[oid] = city
    return cities


def app_names(settings: Settings) -> dict[str, str]:
    """ "client id=Name;client id=Name", and the Azure CLI when it is allowed."""
    pairs = (item.partition("=") for item in settings.mcp_apps.split(";"))
    names = {key.strip(): name.strip() for key, _, name in pairs if key.strip() and name.strip()}
    return {**names, AZURE_CLI_CLIENT_ID: "Azure CLI"} if settings.mcp_allow_cli else names


def none_in(city: str) -> str:
    return f"There is none in stock in {city}."


def tools(app: FastAPI, cities: dict[str, str]) -> list[DoorTool]:
    data, hub = app.state.data, app.state.hub
    request = Request({"type": "http", "app": app})  # what the store and the writes of the staff are reached by

    def note(kind: str, **values: str | None) -> None:
        who = calling()
        name = DEALER_AGENT if who.application else who.name or NO_NAME
        hub.add(kind, name, client=who.app, correlation_id=correlation_id.get(), **values)

    def own_city() -> str:
        city = cities.get(calling().oid)
        if city is None:
            note("refused")
            raise Refused(NO_CITY)
        return city

    @contextmanager
    def refusing(kind: str, city: str, **values: str | None) -> Iterator[None]:
        """Says no for an error of the store, and for what the staff page would not do either."""
        try:
            yield
            return
        except StoreUnavailable:
            text = UNAVAILABLE
        except AlreadyDelivered:
            text = ALREADY_DELIVERED
        except OutOfStock:
            text = none_in(city)
        except NotFound:
            text = UNKNOWN_ORDER if kind == "deliver_asked" else "Unknown stock."
        except UnknownModel:
            text = UNKNOWN_MODEL
        note(kind, city=city, result=text, **values)
        raise Refused(text)

    async def my_stock() -> dict:
        city = own_city()
        with refusing("stock", city):
            store = await ready_store(request)
            rows = data.stock_rows(await store.stock(), None, city)
        note("stock", city=city, result=str(sum(row.in_stock for row in rows)))
        return {"city": city, "cars": [{"model": row.model, "in_stock": row.in_stock} for row in rows]}

    async def change_stock(
        model: Annotated[str, Field(json_schema_extra={"enum": data.models})], change: Change
    ) -> dict:
        city = own_city()
        with refusing("change_asked", city):
            data.check(model, None)
        with refusing("change_asked", city, model=model):
            store = await ready_store(request)
            left = await writing(request, store.change_stock(model, city, change))
        note("stock_changed", model=model, city=city, result=str(left))
        hub.changed()
        return {"model": model, "city": city, "in_stock": left}

    async def open_orders(after: Number | None = None) -> dict:
        city = own_city()
        with refusing("open_orders", city):
            store = await ready_store(request)
            await app.state.keeper.purge()
            stock = await store.stock()
            orders = [order for order in reversed(newest_first(await store.orders())) if order.status == "open"]
        if after:
            orders = [order for order in orders if digits(order.number) > digits(after)]
        shown = orders[:ORDERS_AT_ONCE]
        note("open_orders", city=city, result=str(len(shown)))
        return {
            "city": city,
            "orders": [
                {
                    "number": order.number,
                    "model": order.model,
                    "price": order.price_sek,
                    "currency": CURRENCY,
                    "ordered": order.created.isoformat(),
                    "in_own_stock": has_model(stock, order.model, city),
                }
                for order in shown
            ],
            "more": len(orders) > len(shown),
        }

    async def deliverable(number: str, city: str) -> Order:
        with refusing("deliver_asked", city):
            store = await ready_store(request)
            order = next((order for order in await store.orders() if order.number == number), None)
            if order is None:
                raise NotFound
        with refusing("deliver_asked", city, model=order.model, number=number):
            if order.status != "open":
                raise AlreadyDelivered
            if not has_model(await store.stock(), order.model, city):
                raise OutOfStock
        return order

    async def question(number: str, ctx: Context) -> Elicit[Yes]:
        """Asked before the delivery. It is run again with the answer."""
        try:
            city = own_city()
            order = await deliverable(number, city)
            if not can_ask(ctx):
                note("deliver_asked", model=order.model, city=city, number=number, result="The app cannot ask")
                raise Refused(CANNOT_ASK)
        except Refused as exc:  # the door takes these from a tool only
            raise ToolError(str(exc)) from None
        return Elicit(f"Deliver order {number}, {order.model.capitalize()}, from the stock in {city}?", Yes)

    async def deliver_order(number: Number, answer: Annotated[ElicitationResult[Yes], Resolve(question)]) -> dict:
        city = own_city()
        if not isinstance(answer, AcceptedElicitation) or answer.data.deliver is not True:
            note("deliver_asked", city=city, number=number, result=NO_YES)
            raise Refused(NOT_DELIVERED)
        with refusing("deliver_asked", city, number=number):  # time has passed
            store = await ready_store(request)
            by = calling().name or NO_NAME
            order = await writing(request, store.deliver(number, city, by, app.state.clock()))
        note("delivered", model=order.model, city=city, number=order.number)
        hub.changed()
        return {"number": order.number, "model": order.model, "city": city, "status": order.status}

    both = frozenset({STAFF, AGENT})
    return [
        DealerTool("my_stock", "My stock", "How many cars of each model the caller's city has.", my_stock, both, SCOPE),
        DealerTool(
            "change_stock", "Change my stock",
            "One car more (1) or one car less (-1) of a model in the caller's city. Never below 0.",
            change_stock, both, SCOPE, read_only=False, idempotent=False,
        ),
        DealerTool(
            "open_orders", "Open orders",
            "Orders that nobody has delivered, the oldest first, at most 50, and if the caller's city has the car. "
            "`after` is the last number seen, to get only the later ones.",
            open_orders, both, SCOPE,
        ),
        DealerTool(
            "deliver_order", "Deliver an order",
            "Delivers one order from the stock of the caller's city. The person is asked for a yes first.",
            deliver_order, frozenset({STAFF}), SCOPE,
            read_only=False, destructive=True, idempotent=False, people_only=True,
        ),
    ]  # fmt: skip


def has_model(stock: Stock, model: str, city: str) -> bool:
    return stock.get(model, {}).get(city, 0) > 0


def digits(number: str) -> int:
    return int(number.removeprefix("VO-"))


def can_ask(ctx: Context) -> bool:
    """A client of an older generation tells nothing of itself here, and the door could not ask it."""
    known = ctx.client_capabilities
    asking = known.elicitation if known else None
    return asking is not None and (asking.form is not None or asking.url is None)


def build_doors(app: FastAPI, settings: Settings, validator: DoorTokenValidator | None = None) -> list[Door]:
    """The door, or nothing without its settings."""
    if not (settings.mcp_dealer_url and settings.mcp_dealer_client_id):
        return []
    url = f"{settings.mcp_dealer_url.rstrip('/')}/mcp/{NAME}"
    client_id = settings.mcp_dealer_client_id
    config = DoorConfig(
        name=NAME,
        url=url,
        tenant_id=settings.tenant_id,
        client_id=client_id,
        identifier=url if settings.mcp_identifier_is_url else f"api://{client_id}",
        scopes=(SCOPE,),
        apps=app_names(settings),
        applications=True,
        issuer=settings.mcp_issuer,
    )

    def on_call(call: Call) -> None:
        if call.result == "denied":
            app.state.hub.add("refused", None, client=call.app, correlation_id=correlation_id.get())

    cities = door_cities(settings.dealer_door_cities, app.state.data)
    return [
        Door(
            config,
            tools(app, cities),
            INSTRUCTIONS,
            gate=Gate(CALLS_AT_ONCE),
            validator=validator,
            rate=RateLimit(CALLS_PER_MINUTE),
            on_call=on_call,
            correlation=correlation_id.get,
        )
    ]
