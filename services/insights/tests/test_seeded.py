"""The made up conversations, and the two scripts that use them."""

import asyncio
import json
import re
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest
import respx

from voltrieve_agent.server_tools import MODEL_SLUGS
from voltrieve_insights.conversations import select
from voltrieve_insights.labels import KINDS, RESULTS, SYSTEM, TOPICS, ModelError, Refused
from voltrieve_insights.masking import mask

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

import labels_check  # noqa: E402
import seed_conversations as seed  # noqa: E402

DATA = seed.load()
EVERY = DATA["labelled"] + DATA["planted"]
TODAY = date(2026, 11, 24)
BASE = "https://project.test/api/projects/p"


def test_there_are_100_labelled_and_50_planted():
    assert (len(DATA["labelled"]), len(DATA["planted"])) == (100, 50)
    assert len({c["id"] for c in EVERY}) == 150
    assert len({c["said"][0] for c in EVERY}) == 150


def test_every_label_is_in_its_list():
    for c in EVERY:
        assert set(labels_check.accepted(c, "topic")) <= set(TOPICS)
        assert set(labels_check.accepted(c, "kind")) <= set(KINDS)
        assert set(labels_check.accepted(c, "result")) <= set(RESULTS)
        assert c["car_model"] in (*MODEL_SLUGS, None)
        assert c.get("stated") in (*MODEL_SLUGS, None)
        assert c["client"] in ("car", "web")


def test_few_labels_have_more_than_one_right_answer():
    lists = [[name for name in labels_check.LABELS if isinstance(c[name], list)] for c in EVERY]
    assert max(len(found) for found in lists) <= 1 and sum(len(found) for found in lists) <= 8
    for c in EVERY:
        for name in labels_check.LABELS:
            if isinstance(c[name], list):
                assert len(c[name]) >= 2 and len(set(c[name])) == len(c[name])


def test_a_stated_car_and_no_right_one_means_several_are_asked_about():
    for c in EVERY:
        if c["car_model"] is None and "stated" in c:
            text = " ".join(c["said"][::2]).lower()
            assert sum(slug in text for slug in MODEL_SLUGS) >= 2, c["id"]
    assert sum(c["car_model"] is None and "stated" in c for c in DATA["labelled"]) >= 2


def test_the_prompt_holds_no_conversation():
    text = seed.DATA.read_text().lower()
    assert "wall box" in text and "wall box" not in SYSTEM.lower()


def test_every_topic_kind_and_result_is_there():
    labelled = DATA["labelled"]
    for name, every in (("topic", TOPICS), ("kind", KINDS), ("result", RESULTS)):
        assert {labels_check.accepted(c, name)[0] for c in labelled} == set(every)
    assert {c["car_model"] for c in labelled} == {*MODEL_SLUGS, None}


def test_both_languages_are_about_half():
    for part in (DATA["labelled"], DATA["planted"]):
        share = sum(c["lang"] == "sv" for c in part) / len(part)
        assert 0.4 <= share <= 0.6


def test_lengths_are_mixed_from_one_to_eight_turns():
    turns = {(len(c["said"]) + 1) // 2 for c in EVERY}
    assert min(turns) == 1 and max(turns) == 8 and len(turns) >= 5


def test_a_handful_tries_to_steer():
    assert 4 <= sum(c.get("steers", False) for c in DATA["labelled"]) <= 10


def test_every_planted_detail_is_in_its_text():
    for c in DATA["planted"]:
        assert c["planted"]
        for detail in c["planted"]:
            assert any(detail in said for said in c["said"]), c["id"]
    assert not any("planted" in c for c in DATA["labelled"])


# Capitalized words of the planted text that are no part of a person's name: first words, cars, products.
NOT_NAMES = set(
    "Hej Bilen Under My The Min Mitt Kan Book Boka Please Test Does Is Har Jag Which Your How "
    "Spark Lumen Ragn Fjord Atlas Prime Plus Pure Care Voltrieve Laddning Schema Gräns Dela Ladda Breakdown "
    "QVI VIN Svara Namn Får Choose".split()
)
_WORD = r"[A-ZÅÄÖ][^\W\d_]+(?:-[A-ZÅÄÖ][^\W\d_]+)*"
_PAIR = re.compile(rf"(?<![^\W_])(?=({_WORD}) ({_WORD}))")


def unlisted(c):
    """Pairs of capitalized words that look like a first and a last name and are in no listed detail."""
    pairs = {pair for said in c["said"] for pair in _PAIR.findall(said) if not set(pair) & NOT_NAMES}
    return {" ".join(pair) for pair in pairs if not any(" ".join(pair) in detail for detail in c["planted"])}


def test_no_name_in_the_planted_text_is_left_out_of_the_list():
    assert {c["id"]: unlisted(c) for c in DATA["planted"] if unlisted(c)} == {}


def test_a_name_that_is_left_out_would_be_seen():
    assert unlisted({"said": ["Kan Saga Ekorrberg komma?"], "planted": ["Mattis Ekorrberg"]}) == {"Saga Ekorrberg"}


def test_the_hard_ways_to_write_a_detail_are_planted():
    details = [d for c in DATA["planted"] for d in c["planted"]]
    assert any(re.fullmatch(r"\w( \w){3,}", d) for d in details)  # a personal plate with spaces
    assert any("snabel-a" in d or " at " in d for d in details)  # an email address in words
    split = [c for c in DATA["planted"] if sum(bool(re.fullmatch(r"[\d -]+", d)) for d in c["planted"]) == 2]
    assert len(split) == 1  # a phone number over two messages
    parts = [d for d in split[0]["planted"] if re.fullmatch(r"[\d -]+", d)]
    assert all("[phone]" not in mask(said) for said in split[0]["said"])
    assert not any(all(part in said for part in parts) for said in split[0]["said"])


def test_no_real_organization_or_known_word():
    text = seed.DATA.read_text().lower()
    for word in ("ncap", "klingon", "odensvi", "saltwick", "brackenfell", "applegarth"):
        assert word not in text


def test_the_planted_details_are_of_every_sort():
    details = [d for c in DATA["planted"] for d in c["planted"]]
    masked = Counter(mask(d) for d in details)
    for sort in ("[email]", "[phone]", "[plate]", "[vin]", "[id number]"):
        assert masked[sort] >= 3
    assert sum(" " in d and mask(d) == d for d in details) >= 40  # names and addresses, which no pattern catches


def test_the_made_up_text_holds_nothing_real():
    text = seed.DATA.read_text()
    assert all(m.endswith("@example.com") for m in re.findall(r"[\w.+-]+@[\w.-]+\w", text))
    for found in re.findall(r"(?:\+46 ?|\b0)[\d][\d\s()-]{6,16}\d", text):
        digits = re.sub(r"\D", "", found)
        national = "0" + digits[2:] if found.startswith("+46") else digits
        # The series the Swedish regulator keeps for fiction.
        assert re.fullmatch(r"07017406(0[5-9]|[1-9]\d)|0313900(6\d\d)|040628(04\d\d)|08465004\d\d", national), found
    assert chr(0x2014) not in text and chr(0x2013) not in text


def test_the_metadata_is_what_the_job_reads():
    plan = seed.plan(EVERY, 4, TODAY)
    assert len(plan) == 150
    for meta, items in plan:
        assert set(meta) <= {"owner_oid", "client", "backend", "car_model", "seeded", "started"}
        assert meta["seeded"] == "true" and meta["client"] in ("car", "web")
        assert meta["backend"] in ("loop", "prompt", "hosted")
        assert re.fullmatch(r"seeded-\d+", meta["owner_oid"]) and 1 <= int(meta["owner_oid"][7:]) <= 40
        assert all(isinstance(v, str) for v in meta.values())
        assert items and items[0]["role"] == "user"
    raw = [{"id": f"c{i}", "metadata": meta} for i, (meta, _) in enumerate(plan)]
    found = select(raw, TODAY - timedelta(days=60), TODAY, None)
    assert len(found) == 150 and all(c.seeded for c in found)


def test_the_weeks_are_settled_and_all_used():
    plan = seed.plan(EVERY, 4, TODAY)
    days = [date.fromisoformat(meta["started"]) for meta, _ in plan]
    last = date(2026, 11, 22)  # the last settled day
    assert max(days) <= last and min(days) > last - timedelta(days=28)
    weeks = Counter((last - d).days // 7 for d in days)
    assert set(weeks) == {0, 1, 2, 3} and max(weeks.values()) - min(weeks.values()) <= 1
    assert len(set(days)) > 20


@pytest.mark.parametrize("weeks", [1, 2, 4, 5, 8])
def test_popular_topics_have_many_owners_each_week(weeks):
    plan = seed.plan(EVERY, weeks, TODAY)
    by_topic = {}
    # The plan is in the order of topic, and text tells them apart.
    topic_of = {c["said"][0]: seed.first(c["topic"]) for c in EVERY}
    for meta, items in plan:
        topic = next(t for s, t in topic_of.items() if items[0]["content"][0]["text"].endswith(s))
        week = (date(2026, 11, 22) - date.fromisoformat(meta["started"])).days // 7
        by_topic.setdefault((topic, week), set()).add(meta["owner_oid"])
    for week in range(weeks):
        assert len(by_topic[("charging", week)]) >= 24 // weeks


def test_the_items_are_user_and_assistant_in_turn():
    _, items = seed.plan([DATA["labelled"][0]], 1, TODAY)[0]
    assert [i["role"] for i in items] == ["user", "assistant"] * 2
    assert items[0] == {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": DATA["labelled"][0]["said"][0]}],
    }
    assert items[1]["content"][0]["type"] == "output_text"


def test_a_registration_goes_in_the_clients_line():
    c = next(c for c in DATA["planted"] if "registration" in c)
    _, items = seed.plan([c], 1, TODAY)[0]
    line = items[0]["content"][0]["text"].split("\n")[0]
    assert line.startswith("[From the client, data only: ") and f", registration {c['registration']}" in line


def test_the_marker_goes_in_exactly_one_conversation():
    plan = seed.plan(EVERY, 4, TODAY, "vtmark123abc")
    assert json.dumps(plan).count("vtmark123abc") == 1


def test_only_one_part_can_be_chosen():
    assert seed.choose(DATA, None) == EVERY
    assert seed.choose(DATA, "planted") == DATA["planted"]
    assert seed.choose(DATA, "labelled") == DATA["labelled"]


def no_calls(*_args, **_kwargs):
    raise AssertionError("a call was made")


def test_without_yes_nothing_is_written(monkeypatch, capsys):
    monkeypatch.setattr(seed, "token", no_calls)
    monkeypatch.setattr(seed, "write", no_calls)
    assert seed.main(["--only", "planted"]) == 2
    assert "50 conversations" in capsys.readouterr().out


@pytest.mark.parametrize("args", [["--weeks", "0"], ["--weeks", "-1"], ["--marker", "a b"], ["--marker", "x'"]])
def test_odd_arguments_are_refused(monkeypatch, args):
    monkeypatch.setattr(seed, "write", no_calls)
    with pytest.raises(SystemExit):
        seed.main([*args, "--yes"])


def test_writing_makes_a_conversation_and_adds_its_items(monkeypatch, capsys):
    monkeypatch.setattr(seed, "token", lambda: "made-up-token")
    monkeypatch.setattr(seed, "endpoint", lambda: BASE)
    with respx.mock(base_url=BASE) as mock:
        made = mock.post("/openai/v1/conversations").respond(200, json={"id": "conv_secret"})
        added = mock.post("/openai/v1/conversations/conv_secret/items").respond(200, json={})
        assert seed.main(["--only", "planted", "--yes"]) == 0
    assert (made.call_count, added.call_count) == (50, 50)
    assert json.loads(made.calls[0].request.content)["metadata"]["seeded"] == "true"
    assert json.loads(added.calls[0].request.content)["items"][0]["role"] == "user"
    out = capsys.readouterr().out
    assert "conv_secret" not in out and DATA["planted"][0]["planted"][0] not in out and "50" in out


def test_a_busy_service_is_asked_again(monkeypatch):
    monkeypatch.setattr(seed, "WAIT", 0)
    with respx.mock(base_url=BASE) as mock, httpx.Client(base_url=BASE) as http:
        mock.post("/openai/v1/conversations").side_effect = [
            httpx.Response(429),
            httpx.Response(200, json={"id": "c1"}),
        ]
        mock.post("/openai/v1/conversations/c1/items").respond(200, json={})
        assert seed.write(http, seed.plan(EVERY[:1], 1, TODAY)) == 1


def test_a_failure_says_how_many_were_written_and_nothing_else(monkeypatch, capsys):
    monkeypatch.setattr(seed, "token", lambda: "made-up-token")
    monkeypatch.setattr(seed, "endpoint", lambda: BASE)
    with respx.mock(base_url=BASE) as mock:
        mock.post("/openai/v1/conversations").respond(403, text="secret body")
        assert seed.main(["--yes"]) == 1
    out = capsys.readouterr()
    assert "0 of 150" in out.out + out.err and "403" in out.out + out.err
    assert "secret body" not in out.out + out.err and "project.test" not in out.out + out.err


class Fake:
    """Answers from the right labels, with one wrong result, one error and an intent that leaks."""

    input_tokens, cached_tokens, output_tokens = 1_000_000, 0, 100_000

    def __init__(self, items, wrong=(), fails=(), leaks=()):
        self.by_text = {mask(c["said"][0]): c for c in items}
        self.wrong, self.fails, self.leaks = wrong, fails, leaks

    async def ask(self, system, data, schema):
        first = json.loads(data.split("\n")[0])["user"]
        c = self.by_text[first]
        if c["id"] in self.fails:
            raise ModelError("the model gave no answer that fits")
        result = "refused" if c["id"] in self.wrong else c["result"]
        intent = f"Wants help for {c['planted'][0]}" if c["id"] in self.leaks else "Wants to know something"
        topic, kind, result = (seed.first(v) for v in (c["topic"], c["kind"], result))
        car_model = c["car_model"] or ("several" if "stated" in c else None)
        return {"topic": topic, "car_model": car_model, "kind": kind, "result": result, "intent": intent}


def test_the_check_counts_all_four_and_each_label():
    items = DATA["labelled"]
    fake = Fake(items, wrong=("a001",), fails=("a002",))
    found = asyncio.run(labels_check.check(fake, items))
    text = labels_check.report(found, [], fake)
    assert "all four right: 98 of 100" in text
    assert "topic: 99 of 100" in text and "result: 98 of 100" in text
    assert re.search(r"a001 +result: got refused, want answered", text)
    assert "a002  error: the model gave no answer that fits" in text
    assert "1,000,000" in text and "USD" in text


def test_the_check_counts_planted_details_before_and_after_the_filter():
    planted = DATA["planted"]
    registered = next(c for c in planted if "registration" in c and c["planted"][0] == c["registration"])
    named = next(c for c in planted if mask(c["planted"][0]) == c["planted"][0] and "registration" not in c)
    fake = Fake(planted, leaks=(registered["id"], named["id"]))
    found = asyncio.run(labels_check.check(fake, planted))
    text = labels_check.report([], found, fake)
    assert "in intents before the filter: 2" in text
    assert "in intents after the filter: 1" in text  # a name is left to the detection of personal data
    assert registered["planted"][0] in text and named["planted"][0] in text


@pytest.mark.parametrize(
    ("detail", "intent", "there"),
    [
        # names: folded letters, NFD, short names, possessive s, in parts
        ("Hjördis Vågmo", "Hjordis Vagmo wants range", True),
        ("Hjördis Vågmo", "Hjo\u0308rdis asks about range", True),
        ("Hjördis Vågmo", "Va\u030agmo's car", True),
        ("Hjördis Vågmo", "Wants help with Vågmos car", True),
        ("Hjördis Vågmo", "Wants a longer range", False),
        ("Torvalda Kvistlund", "Wants Kvistlund to call", True),
        ("Torvalda Kvistlund", "torvalda’s question", True),
        ("Torvalda Kvistlund", "Wants to charge at home", False),
        ("Barnaby Thornquist-Lee", "Lee wants to know a code", True),
        ("Barnaby Thornquist-Lee", "Wants a fault code to be deleted", False),
        ("Siv-Ragna Kornby", "Siv asks for a time", True),
        ("Siv-Ragna Kornby", "Wants a test drive of a Ragn", False),
        ("Tyra Blåsippdal", "Tyra wants all deleted", True),
        ("Tyra Blåsippdal", "Wants tyre pressure", False),
        ("Saga Ekorrberg", "Saga may change the schedule", True),
        ("Arvidsson-Lönnkrona", "Arvidsson wants a tow bar", True),
        ("Arvidsson-Lönnkrona", "Wants Lonnkrona's tow bar", True),
        # addresses and places: common words do not count alone
        ("9 Bellmaker Steps", "Wants the steps to order blades", False),
        ("9 Bellmaker Steps", "Lives at Bellmaker", True),
        ("9 Bellmaker Steps", "Lives at 9 bellmaker steps", True),
        ("17 Copperleaf Row", "Lives at Copperleaf", True),
        ("17 Copperleaf Row", "Wants a row of seats", False),
        ("3 Heronsgate Wharf", "Wants delivery to a wharf", False),
        ("Ödhem", "Lives in Odhem", True),
        ("Ödhem", "Wants good hemp seats", False),
        ("Nanny Tjärnlycka", "Wants a nanny seat", False),
        ("Nanny Tjärnlycka", "Tjarnlycka asks for hours", True),
        # plates
        ("QVI 123", "Asks about qvi123", True),
        ("QVI 123", "Asks about QVI-123's service", True),
        ("QVI 123", "Asks about 123 things", False),
        ("LILLAN", "Asks about Lillan", True),
        ("LILLAN", "Asks about L I L L A N", True),
        ("LILLAN", "Lillan’s wipers", True),
        ("LILLAN", "Asks about Lillandia", False),
        ("T U F F I S", "Tuffis has belts", True),
        ("T U F F I S", "T-U-F-F-I-S", True),
        ("T U F F I S", "Wants stuff I sell", False),
        # phone numbers in both forms
        ("070-174 06 05", "Call 070 174 06 05", True),
        ("070-174 06 05", "Call +46701740605", True),
        ("070-174 06 05", "Call +46 (0)70-174 06 05", True),
        ("070-174 06 05", "Call 0046 70 174 06 05", True),
        ("+46 70 174 06 31", "Call 070-1740631", True),
        ("+46 70 174 06 31", "Call 070-1740632", False),
        ("070-174 06 05", "Wants 174 km of range", False),
        ("08-465", "Starts with 08-465", True),
        # id numbers
        ("19901332-4321", "Born 901332-4321", True),
        ("901332-1234", "Person 199013321234", True),
        ("901332-1234", "Person 901332-9999", False),
        # VINs, whole and in part
        ("VTRZZZ00000000017", "VIN vtrzzz 00000000017", True),
        ("VTRZZZ00000000017", "VIN ending 00000017", True),
        ("VTRZZZ00000000017", "VIN starting VTRZZZ00", True),
        ("VTRZZZ00000000017", "VIN starting VTRZZZ0", False),
        # email addresses, also in words
        ("a.b@example.com", "Mail a.b@example.com", True),
        ("odwin.farrowmere@example.com", "Farrowmere wants to charge", True),
        ("odwin.farrowmere@example.com", "Wants an example", False),
        ("ottilia punkt bjorksvans snabel-a example punkt com", "Mail ottilia at example", True),
        ("ottilia punkt bjorksvans snabel-a example punkt com", "Björksvans wants to tow", True),
        ("ottilia punkt bjorksvans snabel-a example punkt com", "Wants an example on com", False),
    ],
)
def test_a_detail_is_found_in_any_form_and_no_common_word_is(detail, intent, there):
    assert labels_check.reached(detail, intent) is there


def test_no_planted_detail_is_found_in_an_ordinary_intent():
    ordinary = (
        "Wants to know how to charge at home from a normal socket",
        "Asks for a test drive of the Spark or Lumen on Saturday",
        "Wants the price of the Ragn, Fjord or Atlas with winter wheels",
        "Wants a warning light explained and the car towed",
        "Asks to turn on the wipers and how long parking is allowed",
        "Wants all stored personal data deleted and help to sign in to the app",
    )
    details = {d for c in DATA["planted"] for d in c["planted"]}
    assert [(d, i) for d in details for i in ordinary if labels_check.reached(d, i)] == []


def test_the_baseline_is_a_model_that_never_names_a_car():
    items = DATA["labelled"]
    want = sum(c.get("stated") == c["car_model"] for c in items)
    fake = Fake(items)
    text = labels_check.report(asyncio.run(labels_check.check(fake, items)), [], fake)
    assert f"car_model of a model that always answers null: {want} of 100" in text
    assert want < 90


def test_one_of_the_accepted_answers_is_right():
    item = {**DATA["labelled"][0], "kind": ["how_to", "problem"]}
    for kind, wrong in (("how_to", []), ("problem", []), ("buy", ["kind"])):
        fake = Fake([{**item, "kind": kind}])
        found = asyncio.run(labels_check.check(fake, [item]))
        assert found[0].wrong == wrong
    text = labels_check.report(found, [], fake)
    assert "kind: got buy, want how_to or problem" in text


def test_a_refused_conversation_is_counted_as_wrong():
    class Refuses(Fake):
        async def ask(self, system, data, schema):
            raise Refused("the service refused the request")

    items = DATA["labelled"][:2]
    fake = Refuses(items)
    text = labels_check.report(asyncio.run(labels_check.check(fake, items)), [], fake)
    assert "all four right: 0 of 2" in text and "a001  error: the service refused the request" in text


def test_the_report_is_saved_without_em_dashes(tmp_path, monkeypatch, capsys):
    fake = Fake(EVERY)
    monkeypatch.setattr(labels_check, "ask_model", lambda data: asyncio.run(labels_check.both(fake, data)))
    out = tmp_path / "labels.txt"
    labels_check.main(["--out", str(out)])
    assert "all four right: 100 of 100" in out.read_text()
    assert out.read_text().strip() in capsys.readouterr().out.strip()
