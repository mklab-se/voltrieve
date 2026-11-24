import random
from collections import Counter

import pytest

from voltrieve_insights.labels import Labelled
from voltrieve_insights.totals import MAX_PER_PERSON, MIN_PEOPLE, totals

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
    assert cuts(people(6) + people(6) + [one_person()] * 2, "all") == [{"People": 5, "Conversations": 15}]


def test_each_person_counts_at_most_three_times_in_each_row():
    assert MAX_PER_PERSON == 3
    items = people(5) + [one_person()] * 40
    assert cuts(items, "all") == [{"People": 5, "Conversations": 5}]  # 4 + 3 is 7, and 45 is not shown
    assert cuts(people(5) * 3, "all") == [{"People": 5, "Conversations": 15}]
    assert cuts(people(5) * 9, "all") == [{"People": 5, "Conversations": 15}]


def test_the_cap_is_for_each_row_and_not_shared_between_rows():
    items = people(5) * 3 + people(5, topic="fault") * 3
    assert cuts(items, "all") == [{"People": 5, "Conversations": 15}]
    assert [r["Conversations"] for r in cuts(items, "topic")] == [15, 15]


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
    many = people(6, car_model="lumen") + people(5, start=6, car_model="lumen")
    few = people(4, start=20, **{"car_model": "lumen", field: other})
    assert other not in repr(cuts(many + few, cut))
    few += people(1, start=24, **{"car_model": "lumen", field: other})
    assert other in repr(cuts(many + few, cut)) and len(cuts(many + few, cut)) == 2


def test_a_model_is_counted_within_its_topic():
    items = people(3, car_model="lumen") + people(3, start=3, car_model="lumen", topic="fault")
    assert cuts(items, "topic_model") == []
    assert len(cuts(items, "all")) == 1


def test_a_conversation_without_a_model_is_in_no_model_row():
    assert cuts(people(9), "topic_model") == []


def test_a_result_of_a_group_with_four_people_has_no_row():
    items = (
        people(5, "Charge at home")
        + people(4, "Charge at home", start=5, result="not_found")
        + people(4, "Charge at home", start=9, result="refused")
    )
    assert cuts(items, "group_result") == [
        {"Topic": "charging", "GroupName": "Charge at home", "Result": "answered", "People": 5, "Conversations": 5}
    ]


def test_a_group_without_a_row_has_no_result_rows():
    items = people(4, "Charge at home") + people(9, start=4)
    assert cuts(items, "group") == []
    assert cuts(items, "group_result") == []


def test_small_groups_are_added_together_as_other_within_their_topic():
    items = people(5, "Charge at home") + people(3, "Charge in the rain", start=5) + people(4, "Find a plug", start=8)
    elsewhere = people(4, "Warning light", start=20, topic="fault") + people(5, start=24, topic="fault")
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
    items += people(1, "Warm the car", start=8, result="refused") + people(1, "Warm the car", start=9)
    assert [(r["GroupName"], r["Result"]) for r in cuts(items, "group_result")] == [
        ("other", "answered"),
        ("other", "refused"),
    ]


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


def test_seven_on_car_and_one_on_web_give_no_client_row():
    items = people(7) + people(1, start=7, client="web")
    assert cuts(items, "client") == []
    assert len(cuts(items, "all")) == 1


def test_one_person_with_forty_conversations_does_not_show():
    rows = totals(people(50) + [one_person("oid-50", client="web")] * 40)
    assert not [r for r in rows if r["Cut"] == "client"]
    assert {r["Conversations"] for r in rows[1:]} == {55}  # 50 + 3 is 53


def test_a_rest_of_four_takes_the_smallest_row_with_it_and_a_rest_of_five_does_not():
    items = people(9) + people(6, start=9, result="partly") + people(4, start=20, result="refused")
    assert [r["Result"] for r in cuts(items, "topic_result")] == ["answered"]
    items += people(1, start=24, result="not_found")
    assert [r["Result"] for r in cuts(items, "topic_result")] == ["answered", "partly"]


def test_of_two_rows_of_the_same_size_the_first_by_its_values_is_taken_out():
    items = people(6) + people(6, start=6, result="partly") + people(1, start=20, result="refused")
    assert [r["Result"] for r in cuts(items, "topic_result")] == ["partly"]


def test_the_rest_is_people_and_not_conversations():
    items = people(9) + [one_person("oid-20", result="refused")] * 9
    assert cuts(items, "topic_result") == []


def test_a_person_in_a_saved_row_is_in_the_rest_too():
    items = people(9) + people(1, result="refused")  # the one person is in both
    assert cuts(items, "topic_result") == []


def test_one_row_taken_out_is_always_enough():
    # It holds the limit itself, also when its people are the same ones as in the rest.
    items = people(9) + people(8, start=20, result="partly") + people(7, start=20, result="handed_off")
    items += people(2, start=20, result="refused")
    assert [r["Result"] for r in cuts(items, "topic_result", limit=7)] == ["answered", "partly"]


def test_conversations_without_a_model_are_the_rest_of_the_models():
    assert cuts(people(9, car_model="lumen") + people(4, start=9), "topic_model") == []
    assert len(cuts(people(9, car_model="lumen") + people(5, start=9), "topic_model")) == 1


def test_a_small_rest_takes_no_topic_and_no_group_with_it():
    items = people(9) + people(1, start=9, topic="fault")
    assert [r["Topic"] for r in cuts(items, "topic")] == ["charging"]
    items = people(9, "Charge at home") + people(1, "Find a plug", start=9) + people(1, start=10)
    assert [r["GroupName"] for r in cuts(items, "group")] == ["Charge at home"]


def test_a_group_result_is_held_back_when_the_rest_of_its_topic_and_result_is_small():
    items = people(9, "Charge at home") + people(6, "Find a plug", start=9) + people(4, start=20)
    assert [r["GroupName"] for r in cuts(items, "group")] == ["Charge at home", "Find a plug"]
    assert [r["GroupName"] for r in cuts(items, "group_result")] == ["Charge at home"]
    assert len(cuts(items + people(1, start=24), "group_result")) == 2


def test_of_two_group_results_of_the_same_size_the_first_by_its_name_is_held_back():
    items = people(6, "Find a plug") + people(6, "Charge at home", start=6) + people(1, start=20)
    assert [r["GroupName"] for r in cuts(items, "group_result")] == ["Find a plug"]


def test_the_rest_of_one_result_holds_back_no_other_result():
    items = people(9, "Charge at home") + people(9, "Charge at home", start=9, result="partly")
    items += people(1, start=20)
    assert [r["Result"] for r in cuts(items, "group_result")] == ["partly"]


def test_a_result_held_back_in_its_topic_is_not_saved_in_a_group_either():
    # 6 answered is held back for the one refused. The row of the group would give the 6 back.
    items = people(6, "Charge at home") + people(7, "Charge at home", start=6, result="partly")
    items += people(7, "Find a plug", start=20, result="partly") + people(1, "Find a plug", start=40, result="refused")
    assert [r["Result"] for r in cuts(items, "topic_result")] == ["partly"]
    assert {r["Result"] for r in cuts(items, "group_result")} == {"partly"}


def test_a_rest_of_many_small_parts_stays_a_known_corner():
    items = people(7, topic="fault")
    for n, result in enumerate(("partly", "not_found", "handed_off", "refused")):
        items += people(4, start=10 * (n + 1), topic="fault", result=result)
    assert cuts(items, "topic_result") == [{"Topic": "fault", "Result": "answered", "People": 5, "Conversations": 5}]


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

CLIENTS = ("car", "cli", "web")  # two would never leave a rest beside a saved row
TOPICS = ("charging", "fault", "other")
MODELS = ("lumen", "terra", None)
KINDS = ("how_to", "buy", "command")
RESULTS = ("answered", "partly", "refused")
GROUPS = ("Charge at home", "Find a plug", "Warning light", "unnamed group", "other", "Other", None)
CUTS = ("run", "all", "client", "topic", "topic_model", "topic_result", "topic_kind", "group", "group_result")
PARENTS = {
    "client": "all",
    "topic": "all",
    "topic_model": "topic",
    "topic_result": "topic",
    "topic_kind": "topic",
    "group": "topic",
    "group_result": "group",
}


OTHER_PARENT = {"group_result": "topic_result"}  # a part of both
HELD_BACK = ("client", "topic_model", "topic_result", "topic_kind", "group_result")  # no rest of 1 to 4


def random_items(rng):
    """Some labels are common and some are rare, and some people talk a lot."""

    def weighted(values):
        weights = [rng.choice((1, 2, 10, 40)) for _ in values]
        return lambda: rng.choices(values, weights)[0]

    owner = weighted([f"oid-{n}" for n in range(rng.randint(1, 60))])
    group, client, topic = weighted(GROUPS), weighted(CLIENTS), weighted(TOPICS)
    model, kind, result = weighted(MODELS), weighted(KINDS), weighted(RESULTS)
    return [
        one_person(owner(), group(), client=client(), topic=topic(), car_model=model(), kind=kind(), result=result())
        for _ in range(rng.randint(0, 160))
    ]


def owners(items):
    return {item.owner for item, _ in items}


def capped(items):
    return sum(min(count, 3) for count in Counter(item.owner for item, _ in items).values())


def small_groups(items, topic, limit):
    here = [(item, name) for item, name in items if item.topic == topic]
    return [
        name
        for name in GROUPS
        if name is not None
        and (name in ("other", "Other") or len(owners([pair for pair in here if pair[1] == name])) < limit)
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


def labels_of(row):
    return {name: row[name] for name in LABELS}


def is_part(row, parent):
    """Whether the row is a part of the parent row. One of a group and result has two parents."""
    return parent["Cut"] in (PARENTS.get(row["Cut"]), OTHER_PARENT.get(row["Cut"])) and all(
        row[name] == value for name, value in labels_of(parent).items() if value
    )


def true_rest(parent, parts, items, limit):
    """The people in the parent row who are in a conversation that is in none of its saved parts."""
    inside = {id(pair) for part in parts for pair in true_items(part, items, limit)}
    return owners([pair for pair in true_items(parent, items, limit) if id(pair) not in inside])


def wanted_rows(items, limit):
    """The rows that should be saved, in order, worked out from the top down."""
    possible = list(possible_rows(items, limit))
    size = {tuple(row.values()): len(owners(true_items(row, items, limit))) for row in possible}

    def hold_back(parent, parts):
        parts = list(parts)
        while parts and 0 < len(true_rest(parent, parts, items, limit)) < limit:
            parts.remove(min(parts, key=lambda row: (size[tuple(row.values())], tuple(row.values()))))
        return parts

    wanted = [row for row in possible if row["Cut"] == "all" and size[tuple(row.values())] >= limit]
    for parent in wanted:  # it grows
        for cut in (c for c, p in PARENTS.items() if p == parent["Cut"]):
            parts = [
                row
                for row in possible
                if row["Cut"] == cut and is_part(row, parent) and size[tuple(row.values())] >= limit
            ]
            wanted.extend(hold_back(parent, parts) if cut in HELD_BACK else parts)

    kept = [row for row in wanted if row["Cut"] != "group_result"]
    for parent in [row for row in wanted if row["Cut"] == "topic_result"]:
        parts = [row for row in wanted if row["Cut"] == "group_result" and is_part(row, parent)]
        kept.extend(hold_back(parent, parts))
    return [row for row in possible if row in kept]


def nearest_five(number):
    return min(range(0, number + 6, 5), key=lambda multiple: abs(multiple - number))


def provable_rest(parent, parts, limit):
    """The fewest people a reader of the saved rows can prove are in the rest of a breakdown."""
    fewest = max(parent["People"] - 2, limit)
    return max(0, fewest - sum(part["People"] + 2 for part in parts))


def test_no_row_and_no_rest_of_200_random_inputs_stands_for_fewer_than_five_people():
    rng = random.Random(20261124)
    seen = Counter()
    for _ in range(200):
        items = [(item, name) for item, name in random_items(rng)]  # each pair is its own object
        asked = rng.choice((1, 5, 5, 8))
        limit = max(asked, 5)
        rows = totals(items, asked)
        assert rows[0] == {"Cut": "run", **dict.fromkeys(LABELS, "")}

        for row in rows[1:]:
            true = true_items(row, items, limit)
            assert len(owners(true)) >= limit
            assert row["People"] == nearest_five(len(owners(true))) > 0
            assert row["Conversations"] == nearest_five(capped(true)) > 0
            seen["saved", row["Cut"]] += 1
            seen["saved", "other"] += row["GroupName"] == "other"

        for parent in rows[1:]:
            for cut in (c for c in PARENTS if parent["Cut"] in (PARENTS[c], OTHER_PARENT.get(c))):
                parts = [row for row in rows if row["Cut"] == cut and is_part(row, parent)]
                rest = len(true_rest(parent, parts, items, limit))
                assert cut not in HELD_BACK or rest == 0 or rest >= limit
                provable = provable_rest(parent, parts, limit)
                assert provable <= rest
                pair = parent["Cut"], cut
                seen["provable"] += bool(parts and provable)
                seen["rest", *pair] += bool(parts and rest >= limit)
                seen["small rest", *pair] += bool(parts and 0 < rest < limit)
                seen["no rest", *pair] += bool(parts and not rest)

        wanted = wanted_rows(items, limit)
        assert [{"Cut": row["Cut"], **labels_of(row)} for row in rows[1:]] == wanted
        saved = [{"Cut": row["Cut"], **labels_of(row)} for row in rows]
        for row in possible_rows(items, limit):
            if row not in saved:
                number = len(owners(true_items(row, items, limit)))
                seen["under", row["Cut"]] += 0 < number < limit
                for parent in rows[1:]:
                    seen["held back", parent["Cut"], row["Cut"]] += number >= limit and is_part(row, parent)

        mixed = list(items)
        rng.shuffle(mixed)
        assert totals(mixed, asked) == rows

    # The test met every kind of row, saved and not, and every kind of rest.
    assert seen["saved", "all"] and seen["saved", "other"] and seen["under", "all"] and seen["provable"]
    for cut, parent in (*PARENTS.items(), *OTHER_PARENT.items()):
        assert seen["saved", cut] and seen["under", cut]
        assert seen["rest", parent, cut] and seen["no rest", parent, cut]
        assert bool(seen["held back", parent, cut]) == (cut in HELD_BACK)
        assert bool(seen["small rest", parent, cut]) == (cut not in HELD_BACK)
