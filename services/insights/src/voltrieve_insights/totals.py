"""The rule of five: what is saved, and what is not.

A row is saved only if at least five different people are in it. Its counts are rounded to the nearest
five, and one person counts as at most three conversations in it. A row holds labels and counts, never
an owner, an intent, an id or a time.

Where the labels that are missing are few and known, the saved parts of a row never leave a rest of one
to four people. Then the smallest part is held back too, and the rest is nobody, or five people or more.
This holds for `client` of `all`, for `topic_model`, `topic_result` and `topic_kind` of `topic`, and for
`group_result` of both its `group` and its `topic_result`.

What a reader can still work out, on purpose:
- `topic` of `all` and `group` of `topic` hold nothing back. The missing labels are many or unknown, and
  one person with another question would empty the page. So a reader can tell that one to four people
  asked about something else, but no row says what.
- A missing row says that the part is under the limit, or was held back with one that is. It can also
  be that nobody asked, and a reader cannot always tell which.
- Rounding hides exact counts. It does not hide that a rest exists, when the parent is clearly larger
  than its saved parts.
- A rest of five or more can be several small parts. With seven answered, and four in each of four other
  results, `answered` is saved and the rest is sixteen. A reader who knows that each part of it is under
  the limit can guess how they split.
- Runs overlap, so two nights can be subtracted.
No more cuts are added, since each one would add a way to subtract.
"""

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from typing import NamedTuple

from voltrieve_insights.labels import Labelled

MIN_PEOPLE = 5  # the limit can be raised, never lowered
ROUND_TO = 5
MAX_PER_PERSON = 3  # conversations of one person that count in a row
OTHER = "other"
CUTS = ("run", "all", "client", "topic", "topic_model", "topic_result", "topic_kind", "group", "group_result")
BREAKDOWNS = {  # each saved row, and the cuts that are parts of it
    "all": ("client", "topic"),
    "topic": ("topic_model", "topic_result", "topic_kind", "group"),
    "group": ("group_result",),
}
NOTHING_HELD_BACK = ("topic", "group")

Row = dict[str, str | int]
Item = tuple[Labelled, str | None]  # a conversation, and the name of its group


class Labels(NamedTuple):
    """The labels of a row. One that does not apply to its cut is an empty string."""

    Cut: str
    Client: str = ""
    Topic: str = ""
    CarModel: str = ""
    RequestKind: str = ""
    Result: str = ""
    GroupName: str = ""


Saved = dict[Labels, list[Item]]

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


def _rounded(count: int) -> int:
    return (count + ROUND_TO // 2) // ROUND_TO * ROUND_TO


def _owners(items: Iterable[Item]) -> set[str]:
    return {item.owner for item, _ in items}


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


def _hold_back(parts: Saved, rest: list[Item], limit: int) -> None:
    """Take the smallest part out, if the people of the rest could be worked out as the parent minus its
    parts. The rest is every conversation of the parent that is in no saved part. One part is always
    enough, since it holds the limit itself."""
    if parts and 0 < len(_owners(rest)) < limit:
        del parts[min(parts, key=lambda labels: (len(_owners(parts[labels])), labels))]


def _breakdown(parent: Labels, items: list[Item], limit: int) -> Saved:
    """The saved rows under a saved row, with the conversations of each. Only a saved row gets parts."""
    found: Saved = {}
    for cut in BREAKDOWNS.get(parent.Cut, ()):
        column, value = LABEL[cut]
        parts = defaultdict(list)
        for item, group in items:
            parts[value(item, group)].append((item, group))
        saved = {
            parent._replace(Cut=cut, **{column: label}): inside
            for label, inside in parts.items()
            if label is not None and len(_owners(inside)) >= limit
        }
        if cut not in NOTHING_HELD_BACK:
            labels = {getattr(part, column) for part in saved}
            _hold_back(saved, [pair for pair in items if value(*pair) not in labels], limit)
        for part, inside in saved.items():
            found |= {part: inside, **_breakdown(part, inside, limit)}
    return found


def _with_results_agreeing(saved: Saved, limit: int) -> Saved:
    """A `group_result` row is also a part of its `topic_result` row, and follows the rule there too. If
    the `topic_result` row was held back, its parts go too, or they would give it back.

    This opens no new gap in the group's own breakdown. A row is only ever taken out, which adds all its
    people to the rest of the group. They are the limit or more, so that rest is then large enough.
    """
    kept = {labels: items for labels, items in saved.items() if labels.Cut != "group_result"}
    for parent, items in saved.items():
        if parent.Cut != "topic_result":
            continue
        parts = {
            labels: inside
            for labels, inside in saved.items()
            if labels.Cut == "group_result" and (labels.Topic, labels.Result) == (parent.Topic, parent.Result)
        }
        names = {labels.GroupName for labels in parts}
        _hold_back(parts, [(item, group) for item, group in items if group not in names], limit)
        kept |= parts
    return kept


def _row(labels: Labels, items: list[Item]) -> Row:
    each = Counter(item.owner for item, _ in items)
    return {
        **labels._asdict(),
        "Conversations": _rounded(sum(min(count, MAX_PER_PERSON) for count in each.values())),
        "People": _rounded(len(each)),
    }


def totals(items: list[Item], limit: int = MIN_PEOPLE) -> list[Row]:
    """The rows of one source, in a fixed order. The `run` row is always there and has no counts."""
    if len({item.seeded for item, _ in items}) > 1:
        raise ValueError("real and made up conversations are never counted together")
    limit = max(limit, MIN_PEOPLE)
    saved: Saved = {}
    if len(_owners(items)) >= limit:
        items = _with_other(items, limit)
        saved = {Labels("all"): items, **_breakdown(Labels("all"), items, limit)}
        saved = _with_results_agreeing(saved, limit)
    order = sorted(saved, key=lambda labels: (CUTS.index(labels.Cut), labels))
    return [Labels("run")._asdict(), *(_row(labels, saved[labels]) for labels in order)]
