"""Mask a chat before a model reads it, and keep an intent only when it cannot point at a person.

Masking is the safe side: a word that looks like a plate may be masked. Nothing here logs or prints text.
"""

import re
import unicodedata

from voltrieve_agent.private import mask_contacts, mask_emails

MAX_INTENT = 100
# Between the letters and the digits of a plate: up to three of whitespace, underscores, hyphens, dashes, the minus
# sign, the soft hyphen and zero-width characters.
_SEP = r"[\s_\-\u00ad\u2010-\u2015\u2212\u200b-\u200d\u2060\ufeff]{0,3}"
# Swedish plate: three letters and three characters (ABC 123, ABC 12D). Underscore does not hide one.
_PLATE = re.compile(r"(?<![^\W_])[^\W\d_]{3}" + _SEP + r"\d{2}[^\W_](?![^\W_])")
# VIN: 17 letters and digits without I, O and Q, with at least one digit. The lookbehind keeps a long run linear.
_VIN = re.compile(r"(?<![A-Za-z0-9])(?=[A-HJ-NPR-Za-hj-npr-z]*[0-9])[A-HJ-NPR-Za-hj-npr-z0-9]{17}(?![A-Za-z0-9])")
_SURROGATES = re.compile("[\ud800-\udfff]")


def without_surrogates(text: str) -> str:
    """The text without lone surrogates, which cannot be encoded. They are taken out and not replaced by a
    sign: a sign in the middle of a plate or a phone number would hide it from the patterns."""
    return _SURROGATES.sub("", text)


def mask(text: str) -> str:
    """The text with contact details, id numbers, plates and VINs replaced. Emails go first and VINs
    before phone numbers, so a phone number cannot eat the first digit of a VIN."""
    masked = _VIN.sub("[vin]", mask_emails(text))
    masked = mask_contacts(masked)
    return _PLATE.sub("[plate]", masked)


def _plain(text: str) -> str:
    """NFKC text without format characters (zero-width, soft hyphen): what a reader sees."""
    return "".join(c for c in unicodedata.normalize("NFKC", text) if unicodedata.category(c) != "Cf")


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


def _alnum(text: str) -> str:
    return "".join(c for c in text if c.isalnum())


def allowed(text: str, registrations: tuple[str, ...]) -> bool:
    """Whether an intent the model wrote may be kept: short, text that can be encoded, no contact detail
    or plate, no run of four digits, and none of the chat's registrations. A registration is looked for
    in any case, with spaces and punctuation between its characters. One of only punctuation is ignored."""
    if without_surrogates(text) != text:
        return False
    seen = _plain(text)
    if len(text) > MAX_INTENT or len(seen) > MAX_INTENT or mask(seen) != seen or _four_digits(seen):
        return False
    lowered = seen.casefold()
    squashed = _alnum(lowered)
    for registration in registrations:
        wanted = _alnum(_plain(registration).casefold())
        if not wanted:
            continue
        if len(wanted) <= 2:
            pattern = r"(?<![^\W_])" + r"[\W_]*".join(re.escape(c) for c in wanted) + r"(?![^\W_])"
            if re.search(pattern, lowered):
                return False
        elif wanted in squashed:
            return False
    return True
