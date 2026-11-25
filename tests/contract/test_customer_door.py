"""The doors of the customer and of Runa: their car is the website's, and a turn of ask_runa cannot order on any backend."""

import json
import re
import sys

import pytest
from app.auth import Claims
from app.clients import MCP
from app.garage import MY_CAR
from app.turns import Job, new_turn_id
from test_contract import ADDRESS, BACKENDS, ORDER, ROOT, FakeDealer, ScriptedModel, api_for
from voltrieve_agent.curated import CURATED_ANSWERS
from voltrieve_agent.model import Completed, FunctionCall, TextDelta
from voltrieve_agent.server_tools import OTHER_REFUSAL

PLACE = FunctionCall("call_1", "place_car_order", '{"offer": "offer-1"}')


def website_car() -> dict:
    source = (ROOT / "clients/web/src/garage.ts").read_text(encoding="utf-8")
    body = re.search(r"^export const MY_CAR: Car = \{\n(.*?)^\};", source, re.M | re.S)
    fields = re.findall(r"^  (\w+): (.+),$", body[1], re.M)
    assert len(fields) == len(body[1].strip().splitlines())
    return {re.sub(r"[A-Z]", lambda m: "_" + m[0].lower(), name): json.loads(value) for name, value in fields}


def test_the_car_of_the_door_is_the_car_of_the_website():
    assert MY_CAR == website_car()


@pytest.mark.parametrize("name", BACKENDS)
@pytest.mark.parametrize("call", [ORDER, PLACE])
async def test_a_turn_of_the_door_cannot_order_whatever_the_role(name, call):
    dealer, model = FakeDealer(), ScriptedModel([call, Completed({})], [TextDelta("ok"), Completed({})])
    app = api_for(name, model, (), dealer=dealer)
    claims = Claims("oid-contract", "", ["TestDrive.Book", "Order.Place"])
    answer = await app.state.turns.answer(
        Job(claims, name, None, new_turn_id(), "Order a Fjord", client=MCP, ordering_client=MCP), 10, lambda: None
    )
    assert answer.text == "ok" and dealer.offers == []
    [result] = model.calls[1]["input"]
    assert result["output"] == OTHER_REFUSAL and ADDRESS not in answer.text


@pytest.mark.parametrize("name", BACKENDS)
async def test_an_approved_answer_is_marked_on_every_backend(name):
    call = FunctionCall("call_1", "get_curated_answer", '{"topic": "contact"}')
    app = api_for(name, ScriptedModel([call, Completed({})]), ())
    claims = Claims("oid-contract", "", [])
    answer = await app.state.turns.answer(Job(claims, name, None, new_turn_id(), "Phone?", client=MCP), 10, lambda: None)
    assert answer.approved and "020-000 000" in answer.text


def test_the_comparison_picks_from_each_group_and_judges_an_answer():
    sys.path.insert(0, str(ROOT / "scripts"))
    import customer_compare as compare

    questions = compare.yaml.safe_load(compare.QUESTIONS.read_text(encoding="utf-8"))["questions"]
    assert [q["group"] for q in compare.pick(questions, 8, "")] == ["docs", "parts", "curated", "none"] * 2
    text = CURATED_ANSWERS["contact"].text
    phone = compare.Result("plain", {"id": "phone", "group": "curated", "topic": "contact"}, set(), set(), f"Hi. {text}")
    changed = compare.Result("Runa", phone.question, {"VP-10007"}, {"VP-10007"}, "VP-10007, VP-99999 " + text.replace(".", "!"))
    assert "yes" in phone.row() and "NO" in changed.row() and "1 of 1" in changed.row() and "VP-99999" in changed.row()
    assert "1 invented" in compare.report([phone, changed], "m", "loop")
