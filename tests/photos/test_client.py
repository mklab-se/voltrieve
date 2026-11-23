"""The shared client: context, tool outputs with attachments, and a photo that is uploaded again."""

import json

import httpx
import pytest

from voltrieve_client import Api, ApiError


def sse(*events) -> str:
    return "".join(f"event: {n}\ndata: {json.dumps(d)}\n\n" for n, d in events)


def make_api(handler) -> Api:
    api = Api("http://api", lambda: "token", "loop")
    api._http = httpx.Client(base_url="http://api", transport=httpx.MockTransport(handler))
    return api


def test_the_context_goes_in_the_body():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, text=sse(("done", {})))

    make_api(handler).turn("hi", context={"speed_kmh": 0}, attachments=["a1"])
    assert seen[0]["context"] == {"speed_kmh": 0} and seen[0]["attachments"] == ["a1"]


def test_without_a_context_the_body_is_as_before():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, text=sse(("done", {})))

    make_api(handler).turn("hi")
    assert set(seen[0]) == {"message", "client_tools"}


def test_a_tool_handler_can_hand_back_attachments():
    bodies = []

    def handler(request):
        bodies.append((request.url.path, json.loads(request.content)))
        if request.url.path == "/chat":
            call = {"turn_id": "t1", "conversation_id": "c1", "calls": [{"call_id": "k1", "name": "get_sign_photo"}]}
            return httpx.Response(200, text=sse(("client_tool_calls", call)))
        return httpx.Response(200, text=sse(("delta", {"text": "ok"}), ("done", {})))

    turn = make_api(handler).turn(
        "look", handlers={"get_sign_photo": lambda arguments: {"output": "The photo is attached.", "attachments": ["a7"]}}
    )
    result = bodies[1][1]["results"][0]
    assert result == {"call_id": "k1", "output": "The photo is attached.", "attachments": ["a7"]}
    assert turn.tool_runs[0][2] == "The photo is attached."


def test_a_tool_handler_that_returns_text_still_works():
    def handler(request):
        if request.url.path == "/chat":
            call = {"turn_id": "t1", "conversation_id": "c1", "calls": [{"call_id": "k1", "name": "honk"}]}
            return httpx.Response(200, text=sse(("client_tool_calls", call)))
        assert json.loads(request.content)["results"] == [{"call_id": "k1", "output": "Honked."}]
        return httpx.Response(200, text=sse(("done", {})))

    make_api(handler).turn("honk", handlers={"honk": lambda arguments: "Honked."})


def fake_server(lost_first: bool = False, rejected: bool = False):
    state = {"uploads": 0, "chats": 0}

    def handler(request):
        if request.url.path == "/attachments":
            state["uploads"] += 1
            if rejected:
                return httpx.Response(200, json={"id": None, "status": "rejected", "code": "too_blurry"})
            return httpx.Response(200, json={"id": f"a{state['uploads']}", "status": "ready"})
        state["chats"] += 1
        if lost_first and state["chats"] == 1:
            return httpx.Response(422, json={"detail": {"code": "unknown_attachment"}})
        ids = json.loads(request.content)["attachments"]
        return httpx.Response(200, text=sse(("delta", {"text": ids[0]}), ("done", {})))

    return state, handler


def test_a_photo_turn_uploads_and_asks():
    state, handler = fake_server()
    result = make_api(handler).turn_with_photo("Can I park?", b"jpeg", "sign", context={"speed_kmh": 0})
    assert result.turn.text == "a1" and not result.reuploaded and result.upload_seconds >= 0 and result.turn_seconds >= 0
    assert state == {"uploads": 1, "chats": 1}


def test_a_lost_attachment_is_uploaded_again_once():
    state, handler = fake_server(lost_first=True)
    result = make_api(handler).turn_with_photo("Can I park?", b"jpeg", "sign")
    assert result.reuploaded and result.turn.text == "a2"
    assert state == {"uploads": 2, "chats": 2}


def test_another_error_is_not_retried():
    def handler(request):
        if request.url.path == "/attachments":
            return httpx.Response(200, json={"id": "a1", "status": "ready"})
        return httpx.Response(422, json={"detail": {"code": "other"}})

    with pytest.raises(ApiError):
        make_api(handler).turn_with_photo("Can I park?", b"jpeg")


def test_a_rejected_photo_makes_no_turn():
    state, handler = fake_server(rejected=True)
    result = make_api(handler).turn_with_photo("Can I park?", b"jpeg")
    assert result.turn is None and result.upload["code"] == "too_blurry"
    assert state == {"uploads": 1, "chats": 0}
