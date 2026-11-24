"""The rule of five: what is saved, and what is not.

A row is saved only if at least five different people are in it. Its counts are rounded to the nearest
five, and one person counts as at most three conversations in it. A row holds labels and counts, never
an owner, an intent, an id or a time.

The parts of a saved row are never saved so that the rest of it is one to four people. Then the smallest
part is held back too. The rest is then nobody, or five people or more. This holds for `client` and
`topic` of `all`, for `topic_model`, `topic_result`, `topic_kind` and `group` of `topic`, and for
`group_result` of `group`.

What a reader can still work out, on purpose:
- A missing row says that the part is under the limit, or was held back with one that is. It can also
  be that nobody asked, and a reader cannot always tell which.
- Rounding hides exact counts. It does not hide that a rest exists, when the parent is clearly larger
  than its saved parts.
- A rest of five or more can be several small parts. With seven answered, and four in each of four other
  results, `answered` is saved and the rest is sixteen. A reader who knows that each part of it is under
  the limit can guess how they split.
- Cuts that are not parent and part are not checked against each other, such as `topic_result` minus its
  `group_result` rows. A rest there can be under five, and large differences show through the rounding.
- Runs overlap, so two nights can be subtracted.
No more cuts are added, since each one would add a way to subtract.
"""

from collections import Counter, defaultdict
from collections.abc import Callable, Iterator

from voltrieve_insights.labels import Labelled

MIN_PEOPLE = 5  # the limit can be raised, never lowered
ROUND_TO = 5
MAX_PER_PERSON = 3  # conversations of one person that count in a row
OTHER = "other"
CUTS = ("run", "all", "client", "topic", "topic_model", "topic_result", "topic_kind", "group", "group_result")
LABELS = ("Client", "Topic", "CarModel", "RequestKind", "Result", "GroupName")

BREAKDOWNS = {  # each saved row, and the cuts that are parts of it
    "all": ("client", "topic"),
    "topic": ("topic_model", "topic_result", "topic_kind", "group"),
    "group": ("group_result",),
}

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


# The label that each breakdown adds to its parent row, and its value. None is no part at all.
LABEL: dict[str, tuple[str, Callable[[Labelled, str | None], str | None]]] = {
    "client": ("Client", lambda item, group: item.client),
    "topic": ("Topic", lambda item, group: item.topic),
    "topic_model": ("CarModel", lambda item, group: item.car_model),
    "topic_result": ("Result", lambda item, group: item.result),
    "topic_kind": ("RequestKind", lambda item, group: item.kind),
    "group": ("GroupName", lambda item, group: group),
    "group_result": ("Result", lambda item, group: item.result),
}


def _owners(items: list[Item]) -> set[str]:
    return {item.owner for item, _ in items}


def _parts(items: list[Item], cut: str, limit: int) -> dict[str, list[Item]]:
    """The parts of one breakdown that may be saved, with the conversations of each.

    The rest is every person in a conversation that is in no saved part. A rest of 1 to limit - 1 people
    could be worked out, as the parent minus its parts, so the smallest part goes into it. One is always
    enough, since it holds the limit itself.
    """
    _, value = LABEL[cut]
    parts = defaultdict(list)
    for item, group in items:
        parts[value(item, group)].append((item, group))
    saved = {v: found for v, found in parts.items() if v is not None and len(_owners(found)) >= limit}
    rest = _owners([item for item in items if value(*item) not in saved])
    if saved and rest and len(rest) < limit:
        del saved[min(saved, key=lambda v: (len(_owners(saved[v])), v))]
    return saved


def _rows(cut: str, labels: dict[str, str], items: list[Item], limit: int) -> Iterator[Row]:
    """The row of these conversations, and the rows of its parts. Only a saved row gets parts."""
    each = Counter(item.owner for item, _ in items)
    yield {
        "Cut": cut,
        **{name: labels.get(name, "") for name in LABELS},
        "Conversations": _rounded(sum(min(count, MAX_PER_PERSON) for count in each.values())),
        "People": _rounded(len(each)),
    }
    for part in BREAKDOWNS.get(cut, ()):
        name, _ = LABEL[part]
        for value, found in _parts(items, part, limit).items():
            yield from _rows(part, {**labels, name: value}, found, limit)


def totals(items: list[Item], limit: int = MIN_PEOPLE) -> list[Row]:
    """The rows of one source, in a fixed order. The `run` row is always there and has no counts.

    A label that does not apply to a cut is an empty string.
    """
    if len({item.seeded for item, _ in items}) > 1:
        raise ValueError("real and made up conversations are never counted together")
    limit = max(limit, MIN_PEOPLE)
    rows: list[Row] = []
    if len(_owners(items)) >= limit:
        rows.extend(_rows("all", {}, _with_other(items, limit), limit))
    rows.sort(key=lambda row: (CUTS.index(row["Cut"]), [row[name] for name in LABELS]))
    return [{"Cut": "run", **dict.fromkeys(LABELS, "")}, *rows]
