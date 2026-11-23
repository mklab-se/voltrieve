"""Attachment handlers: identify an upload by its content and turn it into content parts.

A new kind is one more handler in HANDLERS.
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
