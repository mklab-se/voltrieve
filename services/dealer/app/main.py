import asyncio
import logging
import time
import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.auth import TokenValidator
from app.data import DealerData
from app.hub import Hub, utc_now
from app.logging_setup import configure_logging, correlation_id
from app.model import StoreUnavailable
from app.routes import DATA_PATH, PARTS_STOCK_PATH, build_router
from app.settings import Settings
from app.site import build_site_router
from app.staff import build_staff_router
from app.store import Store, build_store
from app.telemetry import annotate_span, configure_telemetry

logger = logging.getLogger("app.access")
CORRELATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


UNAVAILABLE = "The dealer system is not available right now."


def preparer(store: Store, data: DealerData, clock: Callable[[], datetime]) -> Callable[[], Awaitable[None]]:
    """Seeds and purges until it has worked once. Two at the same time do no harm."""
    done = False

    async def ready() -> None:
        nonlocal done
        if not done:
            await store.seed(data.opening_stock)
            await store.purge(clock())
            done = True

    return ready


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
    store: Store | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> FastAPI:
    configure_logging(settings.log_level)
    configure_telemetry(settings.applicationinsights_connection_string)
    data = DealerData.load(DATA_PATH, PARTS_STOCK_PATH)
    store = store or build_store(settings)
    ready = preparer(store, data, clock)

    async def prepare() -> None:
        try:
            await ready()
        except Exception as exc:  # tried again at the first use
            logger.warning("The store was not ready at startup: %s", type(exc).__name__)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # In the background, so that /healthz answers while the store does not.
        preparing = asyncio.create_task(prepare())
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
    app.state.data, app.state.store, app.state.ready = data, store, ready
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

    @app.middleware("http")
    async def correlation_and_access_log(request: Request, call_next):
        supplied = request.headers.get("X-Correlation-Id", "")
        cid = supplied if CORRELATION_ID_PATTERN.fullmatch(supplied) else str(uuid.uuid4())
        token = correlation_id.set(cid)
        annotate_span(correlation_id=cid)
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Correlation-Id"] = cid
            if request.url.path.startswith("/staff/"):  # names and addresses
                response.headers["Cache-Control"] = "no-store"
            return response
        finally:
            logger.info(
                "request",
                extra={
                    "path": request.url.path,
                    "status": status,
                    "elapsed_ms": round((time.perf_counter() - start) * 1000, 2),
                },
            )
            correlation_id.reset(token)

    app.include_router(build_router(data))
    app.include_router(build_staff_router(data))
    app.include_router(build_site_router(settings))
    return app


def build_app() -> FastAPI:
    """The uvicorn entry point (`uvicorn --factory app.main:build_app`): nothing is built at
    import."""
    return create_app(Settings())
