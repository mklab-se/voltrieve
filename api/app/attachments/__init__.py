"""Attachment handlers: identify an upload by its content and turn it into content parts.

A new kind is one more handler in HANDLERS. A kind above 8 MB also needs the limit in main.py raised,
and text that a handler produces must be cleaned by the handler.
"""

from .base import AttachmentHandler, Part, Rejected
from .photo import PhotoHandler

HANDLERS: tuple[AttachmentHandler, ...] = (PhotoHandler(),)


def identify(head: bytes, handlers: tuple[AttachmentHandler, ...] = HANDLERS) -> AttachmentHandler | None:
    for handler in handlers:
        if handler.accepts(head):
            return handler
    return None


__all__ = ["AttachmentHandler", "HANDLERS", "Part", "Rejected", "identify"]
