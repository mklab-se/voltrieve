"""What the loop keeps of a turn whose model calls are not stored in the conversation.

A turn that carries an attachment is unstored: its calls read the conversation but add nothing to
it, so the loop sends the turn's items itself on every call, and records only text at the end.
Nothing here may be logged or traced. The fields are left out of the repr for that reason.
"""

from dataclasses import dataclass, field

ATTACHMENT_NOTE = "\n(attachment sent)"


def message_item(text: str, parts: list[dict]) -> dict:
    content = [{"type": "input_text", "text": text}] if text else []
    return {"type": "message", "role": "user", "content": [*content, *parts]}


def assistant_item(text: str) -> dict:
    return {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": text}],
    }


def _is_output(item: dict) -> bool:
    return item.get("type") == "function_call_output"


@dataclass
class Unstored:
    """`items` is sent on the next call, `head` is recorded before the answer, and `text` is the
    answer so far."""

    items: list[dict] = field(repr=False)
    head: list[dict] = field(repr=False)
    text: str = field(default="", repr=False)

    @classmethod
    def begin(
        cls, input: str | list[dict], parts: list[dict], kept: dict | None
    ) -> "Unstored | None":
        """The state of a turn that starts or continues, or None when it is stored as usual."""
        if not parts and not kept:
            return None
        if kept:
            unstored = cls.from_dict(kept)
            unstored.items += input
        elif isinstance(input, str):
            head = [message_item(input + ATTACHMENT_NOTE, [])]
            return cls([message_item(input, parts)], head)
        else:
            # The calls these outputs answer were stored, so the outputs must be recorded too.
            unstored = cls(list(input), [i for i in input if _is_output(i)])
        if parts:
            unstored.items.append(message_item("", parts))
        return unstored

    @classmethod
    def from_dict(cls, data: dict) -> "Unstored":
        return cls(list(data["items"]), list(data["head"]), data["text"])

    def to_dict(self) -> dict:
        return {"items": list(self.items), "head": list(self.head), "text": self.text}

    def record_items(self, approved: str = "") -> list[dict]:
        """What the conversation gets at the end. An approved answer keeps its own message, word
        for word, after any text the user has already seen."""
        answers = [assistant_item(text) for text in (self.text, approved) if text.strip()]
        if not answers and not any(_is_output(i) for i in self.head):
            return []
        return [*self.head, *answers]
