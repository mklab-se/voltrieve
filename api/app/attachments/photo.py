"""The photo handler. Photo bytes are never logged here.
"""

import base64
import io

from PIL import Image, ImageFilter, ImageOps, ImageStat

from .base import Part, Rejected

MAX_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 25_000_000
MIN_SIDE = 200
LONG_SIDE = 1600
JPEG_QUALITY = 85
SHARPNESS_SIDE = 1000
# Variance of the Laplacian on the gray picture, border cropped. Sharp pictures measure 122
# and up (23 real and repository pictures 290 and up), blurred ones 31 and down.
MIN_SHARPNESS = 60.0
FORMATS = ("JPEG", "PNG", "WEBP")

MESSAGES = {
    "too_small": "The photo is too small. Move closer and take a new one.",
    "too_blurry": "The photo is blurred. Hold still or slow down and take a new one.",
    "unreadable": "The photo could not be read.",
}


def sharpness(image: Image.Image) -> float:
    gray = image.convert("L")
    gray.thumbnail((SHARPNESS_SIDE, SHARPNESS_SIDE))
    edges = gray.filter(ImageFilter.Kernel((3, 3), (0, 1, 0, 1, -4, 1, 0, 1, 0), 1, 128))
    # The filter copies the outermost pixels unfiltered, so a bright frame would add variance.
    edges = edges.crop((1, 1, edges.width - 1, edges.height - 1))
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
            image = self._open(data)
        except Rejected:
            raise
        except Exception:  # any Pillow decoding problem, bomb errors and truncation included
            raise Rejected("unreadable", MESSAGES["unreadable"]) from None
        if min(image.size) < MIN_SIDE:
            raise Rejected("too_small", MESSAGES["too_small"])
        if sharpness(image) < MIN_SHARPNESS:
            raise Rejected("too_blurry", MESSAGES["too_blurry"])
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
            raise Rejected("unreadable", MESSAGES["unreadable"])
        image.load()
        return ImageOps.exif_transpose(image).convert("RGB")
