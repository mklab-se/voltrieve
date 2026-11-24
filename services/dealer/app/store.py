"""Stock, offers and orders. The memory store is for tests and local work."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from typing import Protocol

from app.model import (
    FIRST_NUMBER,
    NotFound,
    Offer,
    Order,
    delivered,
    newest_first,
    offer_is_stale,
    order_expired,
    order_from,
    stock_after,
)
from app.settings import Settings
from app.table_store import open_table_store

Stock = dict[str, dict[str, int]]  # model, city, number of cars


class Store(Protocol):
    async def seed(self, stock: Stock) -> None:
        """Save this stock when there is none."""

    async def stock(self) -> Stock: ...

    async def change_stock(self, model: str, city: str, change: int) -> int:
        """The new number. Raises NotFound or OutOfStock."""

    async def save_offer(self, offer: Offer) -> None:
        """Also purges, as of the time the offer was made."""

    async def place(self, offer_id: str, customer: str, turn: str, now: datetime) -> tuple[Order, bool]:
        """The order, and whether it is new. Raises NotFound, SameTurn or OfferExpired."""

    async def orders(self) -> list[Order]:
        """Newest first."""

    async def deliver(self, number: str, city: str, by: str, now: datetime) -> Order:
        """Takes one car from the stock. Raises NotFound, AlreadyDelivered or OutOfStock."""

    async def delete(self, number: str) -> Order:
        """Removes the order and its offer, not the stock. Raises NotFound."""

    async def reset(self, stock: Stock) -> None:
        """Removes all offers and orders and restores the stock. The numbering goes on."""

    async def purge(self, now: datetime) -> None:
        """Removes old orders and stale offers."""

    async def close(self) -> None: ...


class MemoryStore:
    """No method waits for anything, so each is one step."""

    def __init__(self):
        self._stock: Stock = {}
        self._offers: dict[str, Offer] = {}
        self._orders: dict[str, Order] = {}
        self._next = FIRST_NUMBER

    async def seed(self, stock: Stock) -> None:
        if not self._stock:
            self._stock = deepcopy(stock)

    async def stock(self) -> Stock:
        return deepcopy(self._stock)

    async def change_stock(self, model: str, city: str, change: int) -> int:
        if city not in self._stock.get(model, {}):
            raise NotFound
        self._stock[model][city] = stock_after(self._stock[model][city], change)
        return self._stock[model][city]

    async def save_offer(self, offer: Offer) -> None:
        await self.purge(offer.created)
        self._offers[offer.id] = offer

    async def place(self, offer_id: str, customer: str, turn: str, now: datetime) -> tuple[Order, bool]:
        offer = self._offers.get(offer_id)
        if offer is None or offer.customer != customer:
            raise NotFound
        if offer.order:
            return self._order(offer.order), False
        order = order_from(offer, self._next, turn, now)
        self._next += 1
        self._orders[order.number] = order
        self._offers[offer.id] = replace(offer, order=order.number)
        return order, True

    async def orders(self) -> list[Order]:
        return newest_first(list(self._orders.values()))

    async def deliver(self, number: str, city: str, by: str, now: datetime) -> Order:
        order = self._order(number)
        in_stock = self._stock.get(order.model, {}).get(city, 0)
        order = delivered(order, in_stock, city, by, now)
        self._stock[order.model][city] = in_stock - 1
        self._orders[number] = order
        return order

    async def delete(self, number: str) -> Order:
        order = self._order(number)
        del self._orders[number]
        self._offers = {key: offer for key, offer in self._offers.items() if offer.order != number}
        return order

    async def reset(self, stock: Stock) -> None:
        self._offers, self._orders, self._stock = {}, {}, deepcopy(stock)

    async def purge(self, now: datetime) -> None:
        self._orders = {key: order for key, order in self._orders.items() if not order_expired(order, now)}
        self._offers = {
            key: offer for key, offer in self._offers.items() if not offer_is_stale(offer, set(self._orders), now)
        }

    async def close(self) -> None:
        pass

    def _order(self, number: str) -> Order:
        if number not in self._orders:
            raise NotFound
        return self._orders[number]


def build_store(settings: Settings) -> Store:
    """The table store never falls back to memory."""
    if settings.store == "memory":
        return MemoryStore()
    if not settings.table_endpoint:
        raise ValueError("TABLE_ENDPOINT is not set")
    return open_table_store(settings.table_endpoint, settings.table_name)
