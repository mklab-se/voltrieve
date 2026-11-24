"""The rule of five: what is saved, and what is not.

A row is saved only if at least five different people are in it, and its counts are rounded to the
nearest five. A row holds labels and counts, never an owner, an intent, an id or a time.

What is left, on purpose: a total minus its parts can hint at a hidden part, about as closely as the
rounding allows. These can be subtracted from each other:
- `all` minus `client`, and `all` minus `topic`
- `topic` minus `topic_model` (the rest also has no model), `topic_result` or `topic_kind`
- `topic` minus `group` (the rest also has no group)
- `group` minus `group_result`
- `topic_result` minus `group_result`
Runs overlap too, so two nights can be subtracted. No more cuts are added, since each one would add a
way to subtract.
"""

from collections import defaultdict
from collections.abc import Iterator

from voltrieve_insights.labels import Labelled

MIN_PEOPLE = 5  # the limit can be raised, never lowered
ROUND_TO = 5
OTHER = "other"
CUTS = ("run", "all", "client", "topic", "topic_model", "topic_result", "topic_kind", "group", "group_result")
LABELS = ("Client", "Topic", "CarModel", "RequestKind", "Result", "GroupName")

Row = dict[str, str | int]
Item = tuple[Labelled, str | None]  # a conversation, and the name of its group


def _rounded(count: int) -> int:
    return (count + ROUND_TO // 2) // ROUND_TO * ROUND_TO


def _with_other(items: list[Item], limit: int) -> list[Item]:
    """The groups under the limit within their topic become `other`. So does one that the model named
    `Other`, or a reader would see two of them."""
    owners = defaultdict(set)
    for item, group in items:
        owners[item.topic, group].add(item.owner)

    def name(item: Labelled, group: str | None) -> str | None:
        if group is None:  # in no group, and so not in other
            return None
        small = len(owners[item.topic, group]) < limit
        return OTHER if small or group.casefold() == OTHER else group

    return [(item, name(item, group)) for item, group in items]


def _places(item: Labelled, group: str | None) -> Iterator[tuple[str, dict[str, str]]]:
    """Every row that the conversation counts in: its cut, and the labels that apply."""
    topic = {"Topic": item.topic}
    yield "all", {}
    yield "client", {"Client": item.client}
    yield "topic", topic
    if item.car_model is not None:
        yield "topic_model", {**topic, "CarModel": item.car_model}
    yield "topic_result", {**topic, "Result": item.result}
    yield "topic_kind", {**topic, "RequestKind": item.kind}
    if group is not None:
        yield "group", {**topic, "GroupName": group}
        yield "group_result", {**topic, "GroupName": group, "Result": item.result}


def totals(items: list[Item], limit: int = MIN_PEOPLE) -> list[Row]:
    """The rows of one source, in a fixed order. The `run` row is always there and has no counts.

    A label that does not apply to a cut is an empty string. A `group_result` row has the people of its
    `group` row or fewer, so it never exists without one.
    """
    if len({item.seeded for item, _ in items}) > 1:
        raise ValueError("real and made up conversations are never counted together")
    limit = max(limit, MIN_PEOPLE)

    owners = defaultdict(list)  # one entry for each conversation, so a person can be there many times
    for item, group in _with_other(items, limit):
        for cut, labels in _places(item, group):
            owners[CUTS.index(cut), tuple(labels.get(name, "") for name in LABELS)].append(item.owner)

    rows: list[Row] = [{"Cut": "run", **dict.fromkeys(LABELS, "")}]
    for (cut, labels), found in sorted(owners.items()):
        people = len(set(found))
        if people >= limit:
            row: Row = {"Cut": CUTS[cut], **dict(zip(LABELS, labels, strict=True))}
            rows.append({**row, "Conversations": _rounded(len(found)), "People": _rounded(people)})
    return rows
