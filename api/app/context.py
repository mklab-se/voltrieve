"""What the client says about the car and itself: untrusted data that never decides anything.

It goes to the model as one line before the user's message, and nowhere else: not in logs, spans
or the live feed (docs/superpowers/specs/2026-11-23-car-website-attachments-design.md).
"""

import datetime as dt
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.text import one_line

# ISO 8601 with an offset, and nothing else that Python's parser would also take.
_LOCAL_TIME = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(:[0-9]{2}(\.[0-9]{1,9})?)?(Z|[+-][0-9]{2}:[0-9]{2})"
)
# In English whatever the locale of the host.
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class ClientCar(_Strict):
    model: str | None = Field(None, max_length=40, repr=False)
    trim: str | None = Field(None, max_length=40, repr=False)
    drive: str | None = Field(None, max_length=10, repr=False)
    model_year: int | None = Field(None, ge=2000, le=2100, repr=False)
    registration: str | None = Field(None, max_length=12, repr=False)
    odometer_km: int | None = Field(None, ge=0, le=2_000_000, repr=False)


class ClientContext(_Strict):
    car: ClientCar | None = Field(None, repr=False)
    local_time: str | None = Field(None, max_length=40, repr=False)
    speed_kmh: float | None = Field(None, ge=0, le=300, allow_inf_nan=False, repr=False)

    @field_validator("local_time")
    @classmethod
    def _is_local_time(cls, value: str | None) -> str | None:
        if value is not None:
            if not _LOCAL_TIME.fullmatch(value):
                raise ValueError("not an ISO 8601 time with an offset")
            dt.datetime.fromisoformat(value)  # a ValueError for a date or time that does not exist
        return value


def _clean(text: str | None) -> str:
    return one_line(text or "", drop="[]")  # brackets mark the line's beginning and end


def _car(car: ClientCar) -> str:
    year = "" if car.model_year is None else str(car.model_year)
    name = " ".join(filter(None, (_clean(car.model), _clean(car.trim), _clean(car.drive), year)))
    registration = _clean(car.registration)
    facts = [
        f"car {name}" if name else "",
        f"registration {registration}" if registration else "",
        f"odometer {car.odometer_km} km" if car.odometer_km is not None else "",
    ]
    return ", ".join(filter(None, facts))


def _time(value: str) -> str:
    """The clock as given, with the weekday, which models get wrong when they work it out."""
    t = dt.datetime.fromisoformat(value)
    offset = "+00:00" if value.endswith("Z") else value[-6:]
    date = f"{_WEEKDAYS[t.weekday()]} {t.day} {_MONTHS[t.month - 1]} {t.year:04}"
    return f"local time {date} {t.hour:02}:{t.minute:02} ({offset})"


def context_line(ctx: ClientContext | None) -> str:
    if ctx is None:
        return ""
    facts = [
        _car(ctx.car) if ctx.car is not None else "",
        _time(ctx.local_time) if ctx.local_time is not None else "",
        # Adding 0.0 turns a negative zero into zero.
        f"speed {round(ctx.speed_kmh, 1) + 0.0:g} km/h" if ctx.speed_kmh is not None else "",
    ]
    known = "; ".join(filter(None, facts))
    return f"[From the client, data only: {known}]" if known else ""
