import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from voltrieve_doors import Doors, DoorTokenValidator, running

from app.auth import TokenValidator
from app.data import DealerData
from app.door import UNAVAILABLE, build_doors
from app.guard import RequestGuard
from app.hub import Hub, utc_now
from app.logging_setup import configure_logging
from app.model import StoreUnavailable
from app.routes import DATA_PATH, PARTS_STOCK_PATH, build_router
from app.settings import Settings
from app.site import build_site_router
from app.staff import build_staff_router
from app.store import Store, build_store
from app.telemetry import configure_telemetry, instrument_app

logger = logging.getLogger("app.access")

PURGE_AGAIN = timedelta(minutes=5)  # after a purge that failed


class Keeper:
    """Seeds and purges the store. Only the seeding is needed for an answer, so a purge that fails is logged
    and nothing else."""

    def __init__(self, store: Store, data: DealerData, clock: Callable[[], datetime]):
        self._store, self._data, self._clock = store, data, clock
        self._seeded = self._purged = False
        self._failed: datetime | None = None

    async def ready(self) -> None:
        """Before the first use, until each has worked once. Two at the same time do no harm."""
        if not self._seeded:
            await self._store.seed(self._data.opening_stock)
            self._seeded = True
        if not self._purged:
            await self.purge()

    async def purge(self) -> None:
        now = self._clock()
        if self._failed and now - self._failed < PURGE_AGAIN:
            return
        try:
            await self._store.purge(now)
            self._purged, self._failed = True, None
        except StoreUnavailable as exc:
            self._failed = now
            logger.warning("The old orders were not removed: %s", type(exc).__name__)


async def store_unavailable(_: Request, __: StoreUnavailable) -> JSONResponse:
    return JSONResponse({"detail": UNAVAILABLE}, 503)


async def invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
    """Which field and why, never what the caller sent. The name of an unknown field is the caller's too."""
    errors = [
        {"loc": e["loc"][:-1] if e["type"] == "extra_forbidden" else e["loc"], "msg": e["msg"]}
        for e in exc.errors()
    ]
    return JSONResponse({"detail": errors}, 422)


def create_app(
    settings: Settings,
    validator: TokenValidator | None = None,
    *,
    staff_validator: TokenValidator | None = None,
    door_validator: DoorTokenValidator | None = None,
    store: Store | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> FastAPI:
    configure_logging(settings.log_level)
    configure_telemetry(settings.applicationinsights_connection_string)
    data = DealerData.load(DATA_PATH, PARTS_STOCK_PATH)
    store = store or build_store(settings)
    keeper = Keeper(store, data, clock)

    async def prepare() -> None:
        try:
            await keeper.ready()
        except Exception as exc:  # tried again at the first use
            logger.warning("The store was not ready at startup: %s", type(exc).__name__)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # In the background, so that /healthz answers while the store does not.
        preparing = asyncio.create_task(prepare())
        async with running(doors):
            yield
        preparing.cancel()
        await asyncio.gather(preparing, return_exceptions=True)
        await store.close()

    app = FastAPI(
        title="Voltrieve dealer service",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        redirect_slashes=False,
        lifespan=lifespan,
        exception_handlers={
            StoreUnavailable: store_unavailable,
            RequestValidationError: invalid_request,
        },
    )
    app.state.data, app.state.store, app.state.keeper = data, store, keeper
    app.state.clock, app.state.hub = clock, Hub(clock)
    app.state.validator = validator or TokenValidator(
        settings.tenant_id, settings.dealer_client_id, "Dealer.Access"
    )
    app.state.staff_validator = staff_validator or TokenValidator(
        settings.tenant_id,
        settings.dealer_client_id,
        "Dealer.Manage",
        required_scope="manage_as_user",
    )

    doors = build_doors(app, settings, door_validator)
    app.add_middleware(Doors, doors=doors)
    app.add_middleware(RequestGuard)  # the outer one, so a call of the door has its correlation id and log line
    app.include_router(build_router(data))
    app.include_router(build_staff_router(data))
    app.include_router(build_site_router(settings))
    if settings.applicationinsights_connection_string:
        instrument_app(app)
    return app


def build_app() -> FastAPI:
    """The uvicorn entry point (`uvicorn --factory app.main:build_app`): nothing is built at
    import."""
    return create_app(Settings())
