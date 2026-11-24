import random
from collections import Counter

import pytest

from voltrieve_insights.labels import Labelled
from voltrieve_insights.totals import MIN_PEOPLE, totals

LABELS = ("Client", "Topic", "CarModel", "RequestKind", "Result", "GroupName")
INTENT = "Wants to charge at home without a wall box"


def one_person(owner="oid-0", group=None, **change):
    labels = {
        "owner": owner,
        "client": "car",
        "seeded": False,
        "topic": "charging",
        "car_model": None,
        "kind": "how_to",
        "result": "answered",
        "intent": INTENT,
    }
    return Labelled(**{**labels, **change}), group


def people(count, group=None, start=0, **change):
    return [one_person(f"oid-{n}", group, **change) for n in range(start, start + count)]


def cuts(items, cut, limit=5):
    """The rows of one cut, with only the fields that apply to it."""
    rows = [r for r in totals(items, limit) if r["Cut"] == cut]
    return [{k: v for k, v in r.items() if k != "Cut" and v != ""} for r in rows]


def test_four_people_are_not_saved_and_five_are():
    assert cuts(people(4), "topic") == []
    assert cuts(people(5), "topic") == [{"Topic": "charging", "People": 5, "Conversations": 5}]


def test_one_person_asking_twenty_times_is_one_person():
    assert cuts([one_person()] * 20, "topic") == []


def test_the_limit_cannot_go_below_five():
    assert cuts(people(4), "topic", limit=1) == []


def test_a_night_with_nobody_still_has_its_run_row():
    assert [r["Cut"] for r in totals([])] == ["run"]


def test_the_limit_can_be_raised():
    assert MIN_PEOPLE == 5
    assert cuts(people(6), "topic", limit=7) == []
    assert len(cuts(people(7), "topic", limit=7)) == 1


def test_four_people_give_no_cut_at_all():
    items = people(4, "Charge at home", car_model="lumen") * 5
    assert [r["Cut"] for r in totals(items)] == ["run"]


def test_the_run_row_has_no_counts_and_there_is_only_one():
    run = {"Cut": "run", **dict.fromkeys(LABELS, "")}
    assert totals([]) == [run]
    assert [r for r in totals(people(9)) if r["Cut"] == "run"] == [run]


@pytest.mark.parametrize("count, saved", [(5, 5), (6, 5), (7, 5), (8, 10), (10, 10), (12, 10), (13, 15)])
def test_people_and_conversations_round_to_the_nearest_five(count, saved):
    assert cuts(people(count), "all") == [{"People": saved, "Conversations": saved}]


def test_people_and_conversations_are_rounded_each_on_its_own():
    assert cuts(people(6) + [one_person()] * 12, "all") == [{"People": 5, "Conversations": 20}]


def test_every_cut_has_its_labels_and_the_rest_are_empty_strings():
    rows = totals(people(5, "Charge at home", car_model="lumen"))
    counts = {"Conversations": 5, "People": 5}
    empty = dict.fromkeys(LABELS, "")
    assert rows == [
        {"Cut": "run", **empty},
        {"Cut": "all", **empty, **counts},
        {"Cut": "client", **empty, "Client": "car", **counts},
        {"Cut": "topic", **empty, "Topic": "charging", **counts},
        {"Cut": "topic_model", **empty, "Topic": "charging", "CarModel": "lumen", **counts},
        {"Cut": "topic_result", **empty, "Topic": "charging", "Result": "answered", **counts},
        {"Cut": "topic_kind", **empty, "Topic": "charging", "RequestKind": "how_to", **counts},
        {"Cut": "group", **empty, "Topic": "charging", "GroupName": "Charge at home", **counts},
        {
            "Cut": "group_result",
            **empty,
            "Topic": "charging",
            "Result": "answered",
            "GroupName": "Charge at home",
            **counts,
        },
    ]


@pytest.mark.parametrize(
    "cut, field, other",
    [
        ("client", "client", "web"),
        ("topic", "topic", "fault"),
        ("topic_model", "car_model", "terra"),
        ("topic_result", "result", "refused"),
        ("topic_kind", "kind", "buy"),
    ],
)
def test_each_value_of_a_cut_needs_five_people_of_its_own(cut, field, other):
    many = people(5, car_model="lumen")
    few = people(4, start=5, **{"car_model": "lumen", field: other})
    assert len(cuts(many + few, cut)) == 1
    assert len(cuts(many + few + people(1, start=9, **{"car_model": "lumen", field: other}), cut)) == 2


def test_a_model_is_counted_within_its_topic():
    items = people(3, car_model="lumen") + people(3, start=3, car_model="lumen", topic="fault")
    assert cuts(items, "topic_model") == []
    assert len(cuts(items, "all")) == 1


def test_a_conversation_without_a_model_is_in_no_model_row():
    assert cuts(people(9), "topic_model") == []


def test_a_result_of_a_group_with_four_people_has_no_row():
    items = people(5, "Charge at home") + people(4, "Charge at home", start=5, result="not_found")
    assert cuts(items, "group_result") == [
        {"Topic": "charging", "GroupName": "Charge at home", "Result": "answered", "People": 5, "Conversations": 5}
    ]


def test_a_group_without_a_row_has_no_result_rows():
    items = people(4, "Charge at home") + people(9, start=4)
    assert cuts(items, "group") == []
    assert cuts(items, "group_result") == []


def test_small_groups_are_added_together_as_other_within_their_topic():
    items = people(5, "Charge at home") + people(3, "Charge in the rain", start=5) + people(4, "Find a plug", start=8)
    elsewhere = people(4, "Warning light", start=20, topic="fault")
    assert cuts(items + elsewhere, "group") == [
        {"Topic": "charging", "GroupName": "Charge at home", "People": 5, "Conversations": 5},
        {"Topic": "charging", "GroupName": "other", "People": 5, "Conversations": 5},
    ]


def test_other_counts_each_person_once_and_needs_five_itself():
    items = people(4, "Charge in the rain") + people(4, "Find a plug")
    assert cuts(items, "group") == []
    items = people(4, "Charge in the rain") + people(4, "Find a plug", start=1)
    assert cuts(items, "group") == [{"Topic": "charging", "GroupName": "other", "People": 5, "Conversations": 10}]


def test_the_results_of_other_need_five_people_each():
    items = people(4, "Charge in the rain") + people(4, "Find a plug", start=4, result="refused")
    assert len(cuts(items, "group")) == 1
    assert cuts(items, "group_result") == []
    assert len(cuts(items + people(1, "Find a plug", start=8, result="refused"), "group_result")) == 1


def test_a_group_the_model_named_other_is_in_the_same_row_as_the_small_ones():
    items = people(6, "Other") + people(4, "Find a plug", start=6)
    assert cuts(items, "group") == [{"Topic": "charging", "GroupName": "other", "People": 10, "Conversations": 10}]


def test_unnamed_group_follows_the_same_rule():
    assert cuts(people(4, "unnamed group"), "group") == []
    assert cuts(people(5, "unnamed group"), "group") == [
        {"Topic": "charging", "GroupName": "unnamed group", "People": 5, "Conversations": 5}
    ]
    items = people(4, "unnamed group") + people(1, "Find a plug", start=4)
    assert [r["GroupName"] for r in cuts(items, "group")] == ["other"]


def test_a_group_is_counted_within_its_topic():
    items = people(3, "Charge at home") + people(3, "Charge at home", start=3, topic="app_account")
    assert cuts(items, "group") == []


def test_an_item_in_no_group_is_not_in_other_either():
    assert cuts(people(9), "group") == []
    assert cuts(people(4, "Find a plug") + people(9, start=4), "group") == []


def test_real_and_made_up_are_never_counted_together():
    with pytest.raises(ValueError) as error:
        totals(people(4, seeded=True) + people(1, start=4))
    assert "oid" not in str(error.value)
    assert len(cuts(people(5, seeded=True), "all")) == 1


def test_no_row_holds_an_owner_an_intent_an_id_or_a_time():
    rows = totals(people(9, "Charge at home", car_model="lumen"))
    for row in rows[1:]:
        assert tuple(row) == ("Cut", *LABELS, "Conversations", "People")
        assert all(type(row[name]) is str for name in ("Cut", *LABELS))
        assert all(type(row[name]) is int for name in ("Conversations", "People"))
    text = repr(rows)
    assert "oid" not in text and INTENT not in text


# The property test. The truth below is a plain count for each possible row, and shares no code with
# the module.

CLIENTS = ("car", "web")
TOPICS = ("charging", "fault", "other")
MODELS = ("lumen", "terra", None)
KINDS = ("how_to", "buy")
RESULTS = ("answered", "partly", "refused")
GROUPS = ("Charge at home", "Find a plug", "Warning light", "unnamed group", "other", None)
CUTS = ("run", "all", "client", "topic", "topic_model", "topic_result", "topic_kind", "group", "group_result")


def random_items(rng):
    owners = [f"oid-{n}" for n in range(rng.randint(1, 40))]
    weights = [rng.randint(1, 6) for _ in GROUPS]
    return [
        one_person(
            rng.choice(owners),
            rng.choices(GROUPS, weights)[0],
            client=rng.choice(CLIENTS),
            topic=rng.choice(TOPICS),
            car_model=rng.choice(MODELS),
            kind=rng.choice(KINDS),
            result=rng.choice(RESULTS),
        )
        for _ in range(rng.randint(0, 120))
    ]


def number_of_people(items):
    return len({item.owner for item, _ in items})


def small_groups(items, topic, limit):
    here = [(item, name) for item, name in items if item.topic == topic]
    return [
        name
        for name in GROUPS
        if name is not None
        and (name == "other" or number_of_people([pair for pair in here if pair[1] == name]) < limit)
    ]


def true_items(row, items, limit):
    """The conversations a row should stand for."""
    found = items
    for name, attribute in (
        ("Client", "client"),
        ("Topic", "topic"),
        ("CarModel", "car_model"),
        ("RequestKind", "kind"),
        ("Result", "result"),
    ):
        if row[name]:
            found = [pair for pair in found if getattr(pair[0], attribute) == row[name]]
    if row["GroupName"] == "other":
        small = small_groups(items, row["Topic"], limit)
        found = [pair for pair in found if pair[1] in small]
    elif row["GroupName"]:
        found = [pair for pair in found if pair[1] == row["GroupName"]]
    return found


def possible_rows(items, limit):
    """Each row there could be, from the fixed values, in the fixed order."""

    def row(cut, **labels):
        return {"Cut": cut, **dict.fromkeys(LABELS, ""), **labels}

    yield row("all")
    yield from (row("client", Client=c) for c in CLIENTS)
    yield from (row("topic", Topic=t) for t in TOPICS)
    yield from (row("topic_model", Topic=t, CarModel=m) for t in TOPICS for m in MODELS if m)
    yield from (row("topic_result", Topic=t, Result=r) for t in TOPICS for r in RESULTS)
    yield from (row("topic_kind", Topic=t, RequestKind=k) for t in TOPICS for k in sorted(KINDS))
    names = {t: sorted({*GROUPS} - {None} - {*small_groups(items, t, limit)} | {"other"}) for t in TOPICS}
    yield from (row("group", Topic=t, GroupName=g) for t in TOPICS for g in names[t])
    yield from (row("group_result", Topic=t, Result=r, GroupName=g) for t in TOPICS for r in RESULTS for g in names[t])


def nearest_five(number):
    return min(range(0, number + 6, 5), key=lambda multiple: abs(multiple - number))


def test_no_row_of_200_random_inputs_stands_for_fewer_than_five_people():
    rng = random.Random(20261124)
    seen, hidden = Counter(), Counter()
    for _ in range(200):
        items = random_items(rng)
        asked = rng.choice((1, 5, 5, 8))
        limit = max(asked, 5)
        rows = totals(items, asked)
        assert rows[0] == {"Cut": "run", **dict.fromkeys(LABELS, "")}

        for row in rows[1:]:
            true = true_items(row, items, limit)
            assert number_of_people(true) >= limit
            assert row["People"] == nearest_five(number_of_people(true)) > 0
            assert row["Conversations"] == nearest_five(len(true)) > 0
            seen[row["Cut"]] += 1
            seen["named other"] += row["GroupName"] == "other"

        wanted = []
        for row in possible_rows(items, limit):
            true = true_items(row, items, limit)
            if number_of_people(true) >= limit:
                wanted.append(row)
            elif true:
                hidden[row["Cut"]] += 1
        assert [{k: r[k] for k in ("Cut", *LABELS)} for r in rows[1:]] == wanted

        mixed = list(items)
        rng.shuffle(mixed)
        assert totals(mixed, asked) == rows

    assert all(seen[cut] for cut in CUTS[1:]) and seen["named other"]  # the test met every kind of row
    assert all(hidden[cut] for cut in CUTS[1:])  # and rows that had to be held back, in every cut
