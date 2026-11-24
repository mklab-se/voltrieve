"""Keep conversation ids out of the logs: httpx writes every URL at INFO."""

import logging

_NOISY = ("httpx", "httpcore", "azure", "azure.core.pipeline.policies.http_logging_policy")


def hush() -> None:
    for name in _NOISY:
        logging.getLogger(name).setLevel(logging.WARNING)
