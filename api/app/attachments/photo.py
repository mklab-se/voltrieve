"""Attachment handlers: identify an upload by its content and turn it into content parts.

The photo is the first kind. A new kind is one more handler in HANDLERS.
Photo bytes are never logged here.
"""

import base64
import io
import warnings
from dataclasses import dataclass
from typing import Protocol

from PIL import Image, ImageFilter, ImageOps, ImageStat

MAX_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 25_000_000
MIN_SIDE = 200
LONG_SIDE = 1600
JPEG_QUALITY = 85
SHARPNESS_SIDE = 1000
# Variance of the Laplacian on the gray picture. Drawn signs measure 129 and up, blurred pictures 29 and down.
MIN_SHARPNESS = 60.0
FORMATS = ("JPEG", "PNG", "WEBP")

MESSAGES = {
    "too_small": "The photo is too small. Move closer and take a new one.",
    "too_blurry": "The photo is blurred. Hold still or slow down and take a new one.",
    "unreadable": "The photo could not be read.",
}


@dataclass(frozen=True)
class Part:
    label: str
    content: dict
    size: int  # bytes
    detail: str = ""  # for a photo, like "1200x1600"


class Rejected(Exception):
    def __init__(self, code: str, message: str | None = None):
        self.code = code
        self.message = message or MESSAGES[code]
        super().__init__(self.message)


class AttachmentHandler(Protocol):
    kind: str
    max_bytes: int

    def accepts(self, head: bytes) -> bool: ...

    def process(self, data: bytes, label: str) -> list[Part]: ...


def sharpness(image: Image.Image) -> float:
    gray = image.convert("L")
    gray.thumbnail((SHARPNESS_SIDE, SHARPNESS_SIDE))
    edges = gray.filter(ImageFilter.Kernel((3, 3), (0, 1, 0, 1, -4, 1, 0, 1, 0), 1, 128))
    return ImageStat.Stat(edges).var[0]


class PhotoHandler:
    kind = "photo"
    max_bytes = MAX_BYTES

    def accepts(self, head: bytes) -> bool:
        return (
            head.startswith(b"\xff\xd8\xff")
            or head.startswith(b"\x89PNG\r\n\x1a\n")
            or (head[:4] == b"RIFF" and head[8:12] == b"WEBP")
        )

    def process(self, data: bytes, label: str) -> list[Part]:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                image = self._open(data)
        except Rejected:
            raise
        except Exception:  # any Pillow decoding problem, bomb warnings and truncation included
            raise Rejected("unreadable") from None
        if min(image.size) < MIN_SIDE:
            raise Rejected("too_small")
        if sharpness(image) < MIN_SHARPNESS:
            raise Rejected("too_blurry")
        image.thumbnail((LONG_SIDE, LONG_SIDE))
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=JPEG_QUALITY)
        jpeg = buffer.getvalue()
        url = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")
        content = {"type": "input_image", "image_url": url, "detail": "high"}
        return [Part(label, content, len(jpeg), f"{image.width}x{image.height}")]

    @staticmethod
    def _open(data: bytes) -> Image.Image:
        image = Image.open(io.BytesIO(data), formats=FORMATS)
        width, height = image.size  # from the header, nothing is decoded yet
        if width * height > MAX_PIXELS:
            raise Rejected("unreadable")
        image.load()
        return ImageOps.exif_transpose(image).convert("RGB")


HANDLERS: tuple[AttachmentHandler, ...] = (PhotoHandler(),)


def identify(head: bytes, handlers: tuple[AttachmentHandler, ...] = HANDLERS) -> AttachmentHandler | None:
    for handler in handlers:
        if handler.accepts(head):
            return handler
    return None
