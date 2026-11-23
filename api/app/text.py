"""Text from a client that goes into the model's input."""

import unicodedata

_INVISIBLE = {"Cc", "Cf", "Cs"}  # control and format characters, and half surrogates


def one_line(text: str, drop: str = "") -> str:
    """`text` on one line, without control characters and the characters in `drop`."""
    kept = [
        " " if c.isspace() else c
        for c in text
        if c.isspace() or (c not in drop and unicodedata.category(c) not in _INVISIBLE)
    ]
    return " ".join("".join(kept).split())
