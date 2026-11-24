"""What our own logs may show of a user and of a private turn.

A turn is private when it carries an attachment or something the client told about itself. What such a
turn says, and any error text that may quote it, stays out of the logs. A failure is shown by its type,
the HTTP status and the stack as frames, without messages, source lines or values.
"""

import hashlib
import logging
import re
import sys
import traceback
from contextvars import ContextVar

# Set by the loop while it runs a private turn.
current_private: ContextVar[bool] = ContextVar("current_private", default=False)

# The lookbehind keeps a long run linear. A match goes on over a + (a@b.se+c@d.se is one match), so
# nothing the plain pattern covered stays readable.
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+\.[\w.-]+(?:\+[\w.+-]*@[\w-]+\.[\w.-]+)*")
_ID_NUMBER = re.compile(r"\b(?:19|20)?\d{6}[-+]?\d{4}\b")  # Swedish personal identity number
# A phone number starts with + or 0 (070-123 45 67, +46 70 123 45 67): prices, dates and part
# numbers do not, so they stay readable.
_PHONE = re.compile(r"(?:\+\d|\b0\d)[\d\s()-]{5,18}\d")
# Swedish forms only. A name ends with one of the words (Storgatan 1, Olof Palmes gata 7), with up to two
# capitalized words before it, and the apartment and postal code after it. ä may be typed as a, or as a
# and a combining mark. Not a distance or a price (körväg 300 km), and not a lower case väg or vägen
# without a name (på väg 2026). Every repeat is bounded, or runs over one word that the lookbehind starts
# once.
_POSTAL_DIGITS = r"(?:SE?-)?[1-9]\d{2}"
_ADDRESS = re.compile(
    r"(?<!\w)(?:(?:[A-ZÅÄÖ][^\W\d_]{2,}[^\S\n]){1,2}|(?!v[äa]\u0308?g(?:en)?(?![^\W\d_])))"
    r"(?i:[^\W\d_]*(?:gatan?|v[äa]\u0308?g(?:en)?|torg(?:et)?|gr(?:ä|a\u0308)nd(?:en)?"
    r"|all(?:é|e\u0301)n?|stigen|backen|kajen|esplanaden)"
    r"\s{0,3}(?:nr\.?\s{0,3})?\d{1,4}(?!\d))"
    r"(?!(?:-\d{1,4})?\s{0,2}(?:km|mil|m|kr|SEK|sek)(?!\w))"
    r"(?:[A-Za-z](?!\w)|\s[A-Z](?!\w))?(?:-\d{1,4}[A-Za-z]?(?!\w))?"
    r"(?i:,?\s(?:lgh|lägenhet|apt)\.?\s?\d{1,4})?"
    rf"(?:,?\s{{1,2}}{_POSTAL_DIGITS}\s?\d{{2}}(?!\w))?"
)
# With its space, so a price or a part number (12500, 529 000, VP-10007) stays readable, and not before
# a tyre's rim, a unit or a count (245 45 R20, 345 40 Nm, 400 20-inch).
_POSTAL_CODE = re.compile(
    rf"(?<![\w-])(?<!\d\s){_POSTAL_DIGITS}\s\d{{2}}(?![\w-])(?!\s\d)(?!\s?%)"
    r"(?!\s(?i:R\d{2}|nm|v|volt|a|w|kw|kwh|km|mil|m|mm|cm|kg|kr|sek|bar|hk|hp|st|pcs"
    r"|inch(?:es)?|tum|percent|procent)(?!\w))"
)

MAX_ERRORS = 8
MAX_FRAMES = 30  # of each error, the ones closest to it


def mask_emails(text: str) -> str:
    """The text with email addresses replaced."""
    return _EMAIL.sub("[email]", text)


def mask_contacts(text: str) -> str:
    """The text with email addresses, phone numbers, Swedish id numbers, street addresses and postal
    codes replaced. Addresses are a best effort: a pattern cannot find every one."""
    masked = mask_emails(text)
    masked = _PHONE.sub(lambda m: "[phone]" if sum(c.isdigit() for c in m[0]) >= 8 else m[0], masked)
    masked = _ID_NUMBER.sub("[id number]", masked)
    return _POSTAL_CODE.sub("[postal code]", _ADDRESS.sub("[address]", masked))


def pseudonym(oid: str) -> str:
    """A short, stable stand-in for an Entra object id, so users can be counted, not named."""
    return hashlib.sha256(f"voltrieve-user:{oid}".encode()).hexdigest()[:12]


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
