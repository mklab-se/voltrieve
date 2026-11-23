"""Swedish parking signs with known answers.

The definitions are the ones the car draws (clients/car/src/sim/signs.json), so a test and the car
agree on what each sign says. This module does two things with them:

  rules, allowed, fee  read the rows of each panel as rules and say what is allowed when
  draw                 draw the sign in a street, as a photo a car could have taken

The reading follows the product owner's: every bordered panel is read together with the top sign,
and a panel does not apply outside its times. Times without parentheses are for weekdays, times in
parentheses for days before a red day, red times for red days. A panel that has times but none for
the kind of day does not apply that day. A time limit counts the minutes of the stay that fall
inside the limit's own times. A fee is judged apart: `allowed` assumes it is paid.

Made-up data only: no real sign, street or client.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[2]
SIGNS_JSON = ROOT / "clients" / "car" / "src" / "sim" / "signs.json"

DAY_TYPES = ("weekday", "before_red", "red")

# --- Red days -------------------------------------------------------------------------------

# Easter Sunday. The days that move with it are counted from here.
EASTER = {2026: dt.date(2026, 4, 5), 2027: dt.date(2027, 3, 28)}
# Midsummer Day and All Saints' Day are the Saturdays from 20 June and 31 October.
MIDSUMMER_DAY = {2026: dt.date(2026, 6, 20), 2027: dt.date(2027, 6, 26)}
ALL_SAINTS = {2026: dt.date(2026, 10, 31), 2027: dt.date(2027, 11, 6)}


def _holidays(year: int) -> set[dt.date]:
    easter = EASTER[year]
    day = dt.timedelta
    return {
        dt.date(year, 1, 1), dt.date(year, 1, 6), dt.date(year, 5, 1), dt.date(year, 6, 6),
        dt.date(year, 12, 25), dt.date(year, 12, 26),
        easter - day(days=2), easter, easter + day(days=1),
        easter + day(days=39),  # Ascension Day
        easter + day(days=49),  # Whit Sunday
        MIDSUMMER_DAY[year], ALL_SAINTS[year],
    }


PUBLIC_HOLIDAYS = _holidays(2026) | _holidays(2027)


def is_red(day: dt.date) -> bool:
    return day.weekday() == 6 or day in PUBLIC_HOLIDAYS


def day_type(day: dt.date) -> str:
    """"red" for Sundays and public holidays, "before_red" for the day before one, else "weekday"."""
    if is_red(day):
        return "red"
    return "before_red" if is_red(day + dt.timedelta(days=1)) else "weekday"


# --- Reading the rows -----------------------------------------------------------------------

DAY_NAMES = ("mån", "tis", "ons", "tor", "fre", "lör", "sön")
MONTHS = ("jan", "feb", "mar", "apr", "maj", "jun", "jul", "aug", "sep", "okt", "nov", "dec")
LIMIT = re.compile(r"^(\d+) (tim|min)$")
TIMES = re.compile(r"^\(?(\d{1,2})-(\d{1,2})\)?$")
SEASON_SLASH = re.compile(r"^(\d{1,2})/(\d{1,2})-(\d{1,2})/(\d{1,2})$")
SEASON_WORDS = re.compile(r"^(\d{1,2}) ([a-zå]+)-(\d{1,2}) ([a-zå]+)$")


@dataclass
class Panel:
    """What one bordered panel says."""

    color: str
    limit_minutes: int | None = None
    fee: bool = False
    charging_only: bool = False
    forbids: bool = False  # a yellow panel with the no parking symbol
    weekday: int | None = None
    season: tuple[tuple[int, int], tuple[int, int]] | None = None
    windows: dict[str, tuple[int, int]] | None = None  # minutes of the day, by kind of day; None: always

    def applies(self, moment: dt.datetime) -> bool:
        if self.weekday is not None and moment.weekday() != self.weekday:
            return False
        if self.season and not _in_season(moment.date(), self.season):
            return False
        if self.windows is None:
            return True
        kind = day_type(moment.date())
        if self.weekday is not None and kind == "before_red":
            kind = "weekday"  # the named day is read as it stands
        window = self.windows.get(kind)
        minute = moment.hour * 60 + moment.minute
        return window is not None and window[0] <= minute < window[1]


@dataclass
class Rules:
    sign: str
    top: str  # "P", "no_parking" or "no_stopping"
    panels: list[Panel] = field(default_factory=list)


def _in_season(day: dt.date, season: tuple[tuple[int, int], tuple[int, int]]) -> bool:
    start, end = season
    here = (day.month, day.day)
    return start <= here <= end if start <= end else here >= start or here <= end


def _month(word: str) -> int:
    return MONTHS.index(word[:3]) + 1


def _read_panel(raw: dict) -> Panel:
    panel = Panel(raw["color"], forbids=raw.get("symbol") == "no_parking")
    texts = [row["text"] for row in raw["rows"]]
    windows: dict[str, tuple[int, int]] = {}
    for row in raw["rows"]:
        text = row["text"]
        lowered = text.lower()
        if match := LIMIT.match(text):
            panel.limit_minutes = int(match[1]) * (60 if match[2] == "tim" else 1)
        elif lowered == "avgift":
            panel.fee = True
        elif match := TIMES.match(text):
            kind = "red" if row.get("red") else "before_red" if text.startswith("(") else "weekday"
            windows[kind] = (int(match[1]) * 60, int(match[2]) * 60)
        elif match := SEASON_SLASH.match(text):
            d1, m1, d2, m2 = map(int, match.groups())
            panel.season = ((m1, d1), (m2, d2))
        elif match := SEASON_WORDS.match(lowered):
            panel.season = ((_month(match[2]), int(match[1])), (_month(match[4]), int(match[3])))
        elif lowered in ("endast", "laddning"):
            pass  # read together, below
        elif lowered[:3] in DAY_NAMES:
            panel.weekday = DAY_NAMES.index(lowered[:3])
        else:
            raise ValueError(f"cannot read the row {text!r}")
    panel.charging_only = {"endast", "laddning"} <= {t.lower() for t in texts}
    panel.windows = windows or None
    return panel


def load_definitions() -> list[dict]:
    return json.loads(SIGNS_JSON.read_text(encoding="utf-8"))


SIGN_IDS = [d["id"] for d in load_definitions()]


def _definition(sign_id: str) -> dict:
    for definition in load_definitions():
        if definition["id"] == sign_id:
            return definition
    raise KeyError(sign_id)


def rules(sign_id: str) -> Rules:
    definition = _definition(sign_id)
    return Rules(sign_id, definition["top"], [_read_panel(p) for p in definition["panels"]])


# --- What is allowed ------------------------------------------------------------------------


def _minutes(start: dt.datetime, minutes: int):
    return (start + dt.timedelta(minutes=i) for i in range(minutes))


def allowed(sign_id: str, when: dt.datetime, minutes: int, charging: bool = False) -> bool:
    """Is a stay of `minutes` from `when` allowed, with any fee paid? Each minute is checked, so a
    stay may cross the end of a window or midnight."""
    sign = rules(sign_id)
    used = [0] * len(sign.panels)
    for moment in _minutes(when, minutes):
        active = [p.applies(moment) for p in sign.panels]
        if sign.top in ("no_parking", "no_stopping") and all(active):
            return False  # the panels only narrow the ban: all of them must apply
        for i, (panel, on) in enumerate(zip(sign.panels, active, strict=True)):
            if not on:
                continue
            if panel.forbids or (panel.charging_only and not charging):
                return False
            used[i] += 1
    return all(
        panel.limit_minutes is None or count <= panel.limit_minutes
        for panel, count in zip(sign.panels, used, strict=True)
    )


def fee(sign_id: str, when: dt.datetime) -> bool:
    """Is a fee to be paid at this moment?"""
    return any(p.fee and p.applies(when) for p in rules(sign_id).panels)


# --- Drawing --------------------------------------------------------------------------------

BLUE, YELLOW, RED = (0, 83, 155), (255, 205, 0), (200, 16, 46)
WHITE, BLACK = (255, 255, 255), (17, 17, 17)
ROW_PX = 40  # the height of a row, the car's close-up has at least 24
CANVAS = (1280, 960)
# All in row heights, as in the car.
TOP, SYMBOL, PADDING, GAP = 4.5, 2, 0.45, 0.25


# A bold sans font with the Swedish letters. The first one found is used (macOS, then Linux).
FONTS = (
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
)


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONTS:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    raise RuntimeError("No bold font found for the signs. Install DejaVu or Liberation Sans.")


def _px(rows: float) -> int:
    return round(rows * ROW_PX)


@dataclass
class SignLayout:
    row_px: int
    width: int
    height: int
    top_size: int
    panels: list[dict]  # y, height, color, symbol y, rows [(text, y, red)]


def layout(sign_id: str) -> SignLayout:
    definition = _definition(sign_id)
    font = _font(round(ROW_PX * 0.8))
    widest = max(
        [font.getlength(r["text"]) for p in definition["panels"] for r in p["rows"]] + [0]
    )
    width = max(_px(TOP), round(widest + 2 * _px(PADDING) + 8))
    y = _px(TOP)
    panels = []
    for panel in definition["panels"]:
        start = y
        y += _px(PADDING)
        symbol_y = None
        if panel.get("symbol"):
            symbol_y = y
            y += _px(SYMBOL) + _px(GAP)
        rows = []
        for i, row in enumerate(panel["rows"]):
            if i:
                y += _px(GAP)
            rows.append((row["text"], y, bool(row.get("red"))))
            y += ROW_PX
        y += _px(PADDING)
        panels.append({"y": start, "height": y - start, "color": panel["color"], "symbol_y": symbol_y,
                       "symbol": panel.get("symbol"), "rows": rows})
    return SignLayout(ROW_PX, width, y, _px(TOP), panels)


def _centered(draw: ImageDraw.ImageDraw, cx: float, cy: float, text: str, size: int, fill) -> None:
    draw.text((cx, cy), text, font=_font(size), fill=fill, anchor="mm")


def _disc(draw: ImageDraw.ImageDraw, x: int, y: int, size: int, kind: str) -> None:
    draw.ellipse((x, y, x + size, y + size), fill=WHITE)
    inset = max(2, round(size * 0.02))
    ring = round(size * 0.1)
    draw.ellipse((x + inset, y + inset, x + size - inset, y + size - inset), fill=RED)
    draw.ellipse((x + inset + ring, y + inset + ring, x + size - inset - ring, y + size - inset - ring), fill=BLUE)
    d = round(size * 0.27)
    cx = cy = size / 2
    bar = max(3, round(size * 0.1))
    draw.line((x + cx - d, y + cy + d, x + cx + d, y + cy - d), fill=RED, width=bar)
    if kind == "no_stopping":
        draw.line((x + cx - d, y + cy - d, x + cx + d, y + cy + d), fill=RED, width=bar)


def _sign(sign_id: str) -> Image.Image:
    lay = layout(sign_id)
    definition = _definition(sign_id)
    image = Image.new("RGB", (lay.width, lay.height), WHITE)
    draw = ImageDraw.Draw(image)
    left = (lay.width - lay.top_size) // 2
    if definition["top"] == "P":
        line = max(2, round(lay.top_size * 0.03))
        draw.rectangle((left, 0, left + lay.top_size - 1, lay.top_size - 1), fill=BLUE)
        draw.rectangle((left + line, line, left + lay.top_size - 1 - line, lay.top_size - 1 - line), outline=WHITE, width=line)
        _centered(draw, left + lay.top_size / 2, lay.top_size * 0.53, "P", round(lay.top_size * 0.8), WHITE)
    else:
        _disc(draw, left, 0, lay.top_size, definition["top"])
    for panel in lay.panels:
        blue = panel["color"] == "blue"
        top, bottom = panel["y"], panel["y"] + panel["height"] - 1
        draw.rectangle((0, top, lay.width - 1, bottom), fill=BLUE if blue else YELLOW)
        line = max(2, ROW_PX // 10)
        draw.rectangle((line, top + line, lay.width - 1 - line, bottom - line), outline=WHITE if blue else RED, width=line)
        if panel["symbol"]:
            size = _px(SYMBOL)
            _disc(draw, (lay.width - size) // 2, panel["symbol_y"], size, panel["symbol"])
        for text, y, red in panel["rows"]:
            if red:  # red on blue cannot be read: a white strip behind it
                draw.rectangle((line * 2, y, lay.width - 1 - line * 2, y + ROW_PX - 1), fill=WHITE)
            ink = RED if red else WHITE if blue else BLACK
            _centered(draw, lay.width / 2, y + ROW_PX / 2, text, round(ROW_PX * 0.8), ink)
    return image


def _street(rng: random.Random) -> Image.Image:
    """A street behind the sign: facades with windows, a pavement, cobbles. All the texture gives the
    picture many edges, as a photo has, so it is far from the server's sharpness limit."""
    w, h = CANVAS
    image = Image.new("RGB", CANVAS, (196, 210, 222))
    draw = ImageDraw.Draw(image)
    for y in range(0, 330, 6):  # sky
        shade = 190 + y // 12
        draw.rectangle((0, y, w, y + 6), fill=(shade - 10, shade, min(255, shade + 14)))
    x = 0
    colors = [(214, 150, 70), (178, 74, 52), (222, 180, 100), (160, 110, 80), (200, 120, 90)]
    while x < w:
        width = rng.randint(220, 340)
        top = rng.randint(40, 140)
        color = rng.choice(colors)
        draw.rectangle((x, top, x + width, 700), fill=color, outline=(60, 50, 45), width=3)
        for wx in range(x + 28, x + width - 60, 84):
            for wy in range(top + 40, 640, 150):
                draw.rectangle((wx, wy, wx + 46, wy + 92), fill=(62, 72, 88), outline=(235, 230, 220), width=4)
                draw.line((wx + 23, wy, wx + 23, wy + 92), fill=(235, 230, 220), width=3)
        x += width
    draw.rectangle((0, 700, w, 780), fill=(150, 150, 148))  # pavement
    draw.rectangle((0, 780, w, h), fill=(72, 72, 76))  # road
    for _ in range(9000):  # stones in the road and wall
        sx, sy = rng.randint(0, w - 1), rng.randint(40, h - 1)
        tone = rng.randint(-34, 34)
        r, g, b = image.getpixel((sx, sy))
        draw.rectangle((sx, sy, sx + rng.randint(2, 7), sy + rng.randint(2, 5)),
                       fill=(max(0, min(255, r + tone)), max(0, min(255, g + tone)), max(0, min(255, b + tone))))
    return image


def draw(sign_id: str, blur: float = 0.0) -> bytes:
    """A JPEG of the sign on its pole in a street. `blur` is a Gaussian radius in pixels."""
    rng = random.Random(sum(map(ord, sign_id)))
    image = _street(rng)
    sign = _sign(sign_id)
    pole_x = round(CANVAS[0] * 0.5)
    top = max(30, (700 - sign.height) // 2 - 10)
    pole = ImageDraw.Draw(image)
    pole.rectangle((pole_x - 8, top, pole_x + 8, 820), fill=(120, 124, 128), outline=(70, 72, 76))
    image.paste(sign, (pole_x - sign.width // 2, top))
    if blur:
        image = image.filter(ImageFilter.GaussianBlur(blur))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=88)
    return buffer.getvalue()
