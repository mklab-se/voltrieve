import logging

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

logger = logging.getLogger(__name__)


def configure_telemetry(connection_string: str) -> bool:
    """Send traces, logs and metrics to Application Insights when a connection string is set.

    FastAPI is left to `instrument_app`: the distro replaces `fastapi.FastAPI`, which is imported
    by then, and takes no options. Without a connection string nothing is exported.
    """
    if not connection_string:
        return False
    from azure.monitor.opentelemetry import configure_azure_monitor

    configure_azure_monitor(
        connection_string=connection_string,
        enable_live_metrics=False,
        instrumentation_options={"fastapi": {"enabled": False}},
    )
    logger.info("Azure Monitor telemetry enabled")
    return True


def instrument_app(app: FastAPI, **options) -> None:
    """One span for a request. It continues the W3C trace context (`traceparent`) sent by the caller,
    so the dealer call shows up inside the API's trace. There is none for what is received and sent:
    an event stream sends for 30 minutes."""
    FastAPIInstrumentor.instrument_app(app, exclude_spans=["receive", "send"], **options)


def annotate_span(**attributes: str | None) -> None:
    """Set attributes on the current span (a no-op when there is none or telemetry is off)."""
    span = trace.get_current_span()
    for key, value in attributes.items():
        if value:
            span.set_attribute(key, value)
