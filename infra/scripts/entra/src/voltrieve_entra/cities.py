"""The list of who has which dealer city at the dealer door: "object id=City;object id=City"."""

import json
import unicodedata
from pathlib import Path


def _same(text: str) -> str:
    """A letter such as ö can be written as one character or as two, so both are made one."""
    return unicodedata.normalize("NFC", text.strip())


def dealer_cities(data: Path) -> list[str]:
    return [_same(city) for city in json.loads(data.read_text(encoding="utf-8"))["cities"]]


def check_cities(text: str, cities: list[str]) -> None:
    """ValueError with a text for the one who set the list. It has no object id in it."""
    seen = set()
    entries = [item.strip() for item in text.split(";") if item.strip()]
    for number, entry in enumerate(entries, 1):
        oid, _, city = entry.partition("=")
        oid, city = oid.strip(), _same(city)
        if not oid or not city:
            raise ValueError(f"entry {number} must be <object id>=<city>")
        if oid in seen:
            raise ValueError(f"entry {number} has the same object id as an earlier one")
        if city not in cities:
            raise ValueError(f"entry {number}: there is no dealer in {city}. The dealers are in {', '.join(cities)}")
        seen.add(oid)
