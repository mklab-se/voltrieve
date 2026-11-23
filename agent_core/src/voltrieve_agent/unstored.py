"""What the loop keeps of a turn whose model calls are not stored in the conversation.

A turn that carries an attachment is unstored: its calls read the conversation but add nothing to
it, so the loop sends the turn's items itself on every call, and records only text at the end.
Nothing here may be logged or traced. The fields are left out of the repr for that reason.
"""

from dataclasses import dataclass, field

ATTACHMENT_TEXT = "(attachment sent)"
ATTACHMENT_NOTE = "\n" + ATTACHMENT_TEXT


def message_item(text: str, parts: list[dict]) -> dict:
    content = [{"type": "input_text", "text": text}] if text else []
    return {"type": "message", "role": "user", "content": [*content, *parts]}


def assistant_item(text: str) -> dict:
    return {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": text}],
    }


def _is_output(item: object) -> bool:
    return isinstance(item, dict) and item.get("type") == "function_call_output"


def _is_text(content: object) -> bool:
    """Whether the content of a user message is text and nothing else."""
    if isinstance(content, str):
        return True
    return isinstance(content, list) and all(
        isinstance(part, dict)
        and part.keys() == {"type", "text"}
        and part["type"] == "input_text"
        and isinstance(part["text"], str)
        for part in content
    )


def _text_only(head: list[dict]) -> list[dict]:
    """The items of `head` with their text alone. The conversation is stored, so anything else is
    replaced, and an item of another kind is left out. Never raises: the user has the answer."""
    items = []
    for item in head:
        if _is_output(item):
            output = item.get("output")
            text = output if isinstance(output, str) else ATTACHMENT_TEXT
            call_id = item.get("call_id")
            items.append({"type": "function_call_output", "call_id": call_id, "output": text})
        elif isinstance(item, dict) and item.get("role") == "user":
            content = item.get("content")
            if _is_text(content):
                items.append({"type": "message", "role": "user", "content": content})
            else:
                items.append(message_item(ATTACHMENT_TEXT, []))
    return items


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
            if isinstance(input, str):
                unstored.items.append(message_item(input, parts))
                return unstored
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
        head = _text_only(self.head)
        if not answers and not any(_is_output(i) for i in head):
            return []
        return [*head, *answers]
