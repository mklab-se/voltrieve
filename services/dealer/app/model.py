"""Offers, orders and the rules that need no store."""

import secrets
import unicodedata
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

OFFER_MINUTES = 30
ORDER_DAYS = 30
FIRST_NUMBER = 1001
ADDRESS_LENGTH = (5, 200)


class NotFound(Exception):
    """No such offer for this customer, no such order, or no such model in that city."""


class SameTurn(Exception):
    """The customer has not answered since the offer was made."""


class OfferExpired(Exception):
    pass


class AlreadyDelivered(Exception):
    pass


class OutOfStock(Exception):
    pass


class StoreUnavailable(Exception):
    """The store gave no usable answer. It never holds or chains the error behind it."""


@dataclass(frozen=True)
class Offer:
    id: str
    customer: str = field(repr=False)  # the oid
    name: str = field(repr=False)
    model: str
    price_sek: int
    address: str = field(repr=False)
    turn: str
    client: str
    created: datetime
    order: str | None  # the number of the order that it became


@dataclass(frozen=True)
class Order:
    number: str
    model: str
    price_sek: int
    name: str = field(repr=False)
    address: str = field(repr=False)
    client: str
    status: str  # "open" or "delivered"
    created: datetime
    city: str | None  # this and the two below are set by the delivery
    delivered: datetime | None
    delivered_by: str | None


def new_offer_id() -> str:
    return secrets.token_urlsafe(9)


def one_line(text: str) -> str:
    """Without control characters."""
    visible = (" " if unicodedata.category(char)[0] in "CZ" else char for char in text)
    return " ".join("".join(visible).split())


def clean_address(text: str) -> str:
    """The error does not repeat the text."""
    line = one_line(text)
    shortest, longest = ADDRESS_LENGTH
    if not shortest <= len(line) <= longest:
        raise ValueError(f"The address must be {shortest} to {longest} characters in one line.")
    return line


def order_from(offer: Offer, number: int, turn: str, now: datetime) -> Order:
    """The order of an offer that has none yet."""
    if turn == offer.turn:
        raise SameTurn
    if offer_expired(offer, now):
        raise OfferExpired
    return Order(
        number=f"VO-{number}",
        model=offer.model,
        price_sek=offer.price_sek,
        name=offer.name,
        address=offer.address,
        client=offer.client,
        status="open",
        created=now,
        city=None,
        delivered=None,
        delivered_by=None,
    )


def placed(offer: Offer, number: str) -> Offer:
    """It only points at the order now. The name and the address are in the order."""
    return replace(offer, order=number, name="", address="")


def delivered(order: Order, in_stock: int, city: str, by: str, now: datetime) -> Order:
    if order.status == "delivered":
        raise AlreadyDelivered
    if in_stock < 1:
        raise OutOfStock
    return replace(order, status="delivered", city=city, delivered=now, delivered_by=by)


def stock_after(in_stock: int, change: int) -> int:
    if in_stock + change < 0:
        raise OutOfStock
    return in_stock + change


def newest_first(orders: list[Order]) -> list[Order]:
    return sorted(orders, key=lambda order: int(order.number.removeprefix("VO-")), reverse=True)


def offer_expired(offer: Offer, now: datetime) -> bool:
    return now - offer.created >= timedelta(minutes=OFFER_MINUTES)


def order_expired(order: Order, now: datetime) -> bool:
    return now - order.created >= timedelta(days=ORDER_DAYS)


def offer_is_stale(offer: Offer, kept_orders: set[str], now: datetime) -> bool:
    """An offer that was not placed goes after 30 minutes, a placed one with its order."""
    return offer.order not in kept_orders if offer.order else offer_expired(offer, now)
