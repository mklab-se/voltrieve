"""Pictures of the Spark, and a blurred sign, with known answers.

The car is the one in the repository's own design pictures. The crack is drawn here, so no real
photo is needed. A made-up car and a made-up damage.
"""

from __future__ import annotations

import io
import random
from pathlib import Path

from PIL import Image, ImageDraw

import signs

ROOT = Path(__file__).resolve().parents[2]
CAR = ROOT / "data" / "design" / "images" / "spark" / "light_detail.jpg"
SIDE = 1024  # the long side of the pictures

# The headlamps in the 1536 by 1024 picture: the car's left is the one near the viewer.
LAMPS = {"left": (695, 445, 815, 555), "right": (215, 430, 295, 540)}


def _jpeg(image: Image.Image) -> bytes:
    image = image.copy()
    image.thumbnail((SIDE, SIDE))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=88)
    return buffer.getvalue()


def _crack(image: Image.Image, box: tuple[int, int, int, int], seed: int) -> None:
    rng = random.Random(seed)
    draw = ImageDraw.Draw(image)
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    for _ in range(9):
        x, y = cx, cy
        dx, dy = rng.uniform(-1, 1), rng.uniform(-1, 1)
        for _ in range(9):
            nx = x + dx * (x1 - x0) / 14 + rng.uniform(-4, 4)
            ny = y + dy * (y1 - y0) / 14 + rng.uniform(-4, 4)
            draw.line((x, y, nx, ny), fill=(250, 250, 250), width=3)
            draw.line((x + 2, y + 2, nx + 2, ny + 2), fill=(15, 15, 15), width=3)
            x, y = nx, ny
    draw.polygon([(cx - 18, cy - 10), (cx + 12, cy - 22), (cx + 20, cy + 12), (cx - 8, cy + 18)], fill=(8, 8, 8))


def broken_lamp(side: str) -> bytes:
    """The Spark with a cracked headlamp on its left or its right."""
    if side not in LAMPS:
        raise ValueError("side must be 'left' or 'right'")
    image = Image.open(CAR).convert("RGB")
    _crack(image, LAMPS[side], seed=4)
    return _jpeg(image)


def undamaged() -> bytes:
    """The same picture with nothing broken."""
    return _jpeg(Image.open(CAR).convert("RGB"))


def blurred(sign_id: str = "two_hours_fee") -> bytes:
    """A sign photographed at speed: the server rejects it as too blurry."""
    return signs.draw(sign_id, blur=14)
