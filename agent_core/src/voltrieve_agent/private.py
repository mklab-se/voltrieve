"""Logging failures of private turns.

A turn is private when it carries an attachment or something the client told about itself. What such a
turn says, and any error text that may quote it, stays out of the logs. A failure is shown by its type,
the HTTP status and the stack as frames, without messages, source lines or values.
"""

import logging
import sys
import traceback
from contextvars import ContextVar

# Set by the loop while it runs a private turn.
current_private: ContextVar[bool] = ContextVar("current_private", default=False)

MAX_ERRORS = 8
MAX_FRAMES = 30  # of each error, the ones closest to it


def _status(exc: BaseException) -> int | None:
    for name in ("status_code", "status"):
        try:
            value = getattr(exc, name, None)
        except Exception:  # a property that fails
            continue
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def frames_only(exc: BaseException) -> str:
    """The error, and the errors behind it, without any of their texts. Never raises: it is used where
    a failure is being handled."""
    try:
        return _frames_only(exc)
    except Exception:
        return type(exc).__name__


def _frames_only(exc: BaseException) -> str:
    lines: list[str] = []
    waiting = [(exc, "")]
    seen: set[int] = set()
    while waiting and len(seen) < MAX_ERRORS:
        error, relation = waiting.pop(0)
        if id(error) in seen:
            continue
        seen.add(id(error))
        status = _status(error)
        about = "" if status is None else f", HTTP status {status}"
        lines.append(f"{relation}{type(error).__name__}{about}")
        frames = [(frame.f_code, line) for frame, line in traceback.walk_tb(error.__traceback__)]
        lines += [f"  {c.co_filename}:{line} in {c.co_name}" for c, line in frames[-MAX_FRAMES:]]
        behind = error.__cause__
        if behind is None and not error.__suppress_context__:
            behind = error.__context__
        if behind is not None:
            waiting.append((behind, "after "))
        if isinstance(error, BaseExceptionGroup):
            waiting += [(member, "with ") for member in error.exceptions]
    return "\n".join(lines)


def log_exception(
    logger: logging.Logger,
    message: str,
    *args: object,
    level: int = logging.ERROR,
    private: bool | None = None,
) -> None:
    """Log the exception being handled: as frames only when `private`, else with its traceback.
    Without `private`, the turn being run decides."""
    if current_private.get() if private is None else private:
        shown = frames_only(sys.exception())
        logger.log(level, f"{message}: %s", *args, shown, stacklevel=2)
    else:
        logger.log(level, message, *args, exc_info=True, stacklevel=2)
