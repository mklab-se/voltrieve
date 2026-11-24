"""Mask a chat before a model reads it, and keep an intent only when it cannot point at a person.

Masking is the safe side: a word that looks like a plate may be masked. Nothing here logs or prints text.
"""

import re
import unicodedata

from voltrieve_agent.private import mask_contacts

MAX_INTENT = 100
# Swedish plate: three letters and three characters (ABC 123, ABC 12D), the space or hyphen is optional.
_PLATE = re.compile(r"(?<!\w)[^\W\d_]{3}[\s-]?\d{2}[^\W_](?!\w)")
# VIN: 17 letters and digits without I, O and Q, with at least one digit. The lookbehind keeps a long run linear.
_VIN = re.compile(r"(?<![A-Za-z0-9])(?=[A-HJ-NPR-Za-hj-npr-z]*[0-9])[A-HJ-NPR-Za-hj-npr-z0-9]{17}(?![A-Za-z0-9])")
_WORDS = re.compile(r"\W+")


def mask(text: str) -> str:
    """The text with contact details, id numbers, plates and VINs replaced."""
    masked = mask_contacts(text)
    masked = _VIN.sub("[vin]", masked)
    return _PLATE.sub("[plate]", masked)


def _squash(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKC", text).casefold() if c.isalnum())


def _four_digits(text: str) -> bool:
    run = 0
    for char in text:
        if char.isdigit():  # any script: fullwidth and Arabic-Indic digits count
            run += 1
            if run >= 4:
                return True
        elif not char.isspace():
            run = 0
    return False


def allowed(text: str, registrations: tuple[str, ...]) -> bool:
    """Whether an intent the model wrote may be kept: short, no contact detail or plate, no run of
    four digits, and none of the chat's registrations."""
    if len(text) > MAX_INTENT or mask(text) != text or _four_digits(text):
        return False
    squashed = _squash(text)
    words = {w for w in _WORDS.split(unicodedata.normalize("NFKC", text).casefold()) if w}
    for registration in registrations:
        wanted = _squash(registration)
        if not wanted:
            literal = registration.strip()
            if literal and literal in text:
                return False
        elif len(wanted) <= 2:
            if wanted in words:
                return False
        elif wanted in squashed:
            return False
    return True
