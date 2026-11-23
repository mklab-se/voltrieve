"""Attachments kept in memory: who owns them, how long they live and how many fit."""

import json
import re

import pytest

from app.attachments import Part
from app.attachments.photo import PhotoHandler
from app.attachments.store import AttachmentStore, attachment_parts
from tests.test_attachment_photo import striped, to_bytes

MB = 1024 * 1024


def part(memory=None, label="front camera", detail="1200x800", text="AAAA", size=100):
    """A part of `size` bytes as a file, and with a content of `memory` bytes."""
    content = {"type": "input_image", "image_url": text}
    if memory is not None:
        content["image_url"] = "A" * (memory - len(json.dumps({**content, "image_url": ""})))
    return Part(label, content, size, detail)


@pytest.fixture
def store(clock):
    return AttachmentStore(clock=clock)


def test_an_attachment_is_found_by_its_owner_until_it_expires(store, clock):
    stored = store.put("anna", "photo", [part()])
    assert stored.kind == "photo" and stored.parts == [part()] and stored.size == 100
    assert stored.expires_at == clock.now + 900
    assert store.get("anna", stored.id) is stored
    clock.now += 899
    assert store.get("anna", stored.id) is stored
    clock.now += 1
    assert store.get("anna", stored.id) is None


def test_unknown_expired_and_other_users_ids_all_give_nothing(store, clock):
    stored = store.put("anna", "photo", [part()])
    assert store.get("bob", stored.id) is None and store.get("anna", "unknown") is None
    assert store.get("anna", stored.id) is stored  # a miss by someone else changes nothing
    clock.now += 900
    assert store.get("anna", stored.id) is None


def test_ids_are_long_random_and_fit_the_id_pattern(store):
    ids = {store.put(f"user{i}", "photo", [part()]).id for i in range(50)}
    assert len(ids) == 50
    assert all(re.fullmatch(r"[A-Za-z0-9._-]{32}", i) for i in ids)


def test_the_users_own_oldest_goes_first(store):
    other = store.put("bob", "photo", [part()])  # older than all of Anna's
    ids = [store.put("anna", "photo", [part()]).id for _ in range(11)]
    assert store.get("anna", ids[0]) is None
    assert all(store.get("anna", i) for i in ids[1:])
    assert store.get("bob", other.id) is other


def test_total_size_is_capped(store):
    others = [store.put(f"user{i}", "photo", [part(10 * MB)]) for i in range(4)]
    first = store.put("anna", "photo", [part(10 * MB)])
    assert store.memory == 50 * MB
    # At the limit the user's own goes first, although it is the newest of all.
    second = store.put("anna", "photo", [part(10 * MB)])
    assert store.get("anna", first.id) is None and store.memory == 50 * MB
    assert all(store.get(f"user{i}", o.id) for i, o in enumerate(others))
    # A user with nothing of their own pushes out the oldest of anyone.
    third = store.put("new", "photo", [part(5 * MB)])
    assert store.get("user0", others[0].id) is None
    assert store.get("user1", others[1].id) and store.get("anna", second.id) and store.get("new", third.id)
    assert store.memory == 45 * MB


def test_the_trace_gets_the_file_size_and_the_cap_counts_the_memory(store):
    """A photo is held as base64, a third larger than the JPEG."""
    stored = store.put("anna", "bundle", [part(1000, size=10), part(2000, size=20)])
    assert stored.summary()["size"] == 30 and store.memory == 3000


def test_a_photo_counts_with_its_encoded_length(store):
    [photo] = PhotoHandler().process(to_bytes(striped(1200, 800)), "")
    stored = store.put("anna", "photo", [photo])
    assert stored.size == photo.size
    assert len(photo.content["image_url"]) <= store.memory <= len(photo.content["image_url"]) + 200
    assert store.memory > photo.size * 1.3


def test_expired_attachments_go_before_anyones_live_one(store, clock):
    old = store.put("bob", "photo", [part(30 * MB)])
    clock.now += 600
    live = store.put("carl", "photo", [part(20 * MB)])
    clock.now += 300
    new = store.put("anna", "photo", [part(30 * MB)])
    assert store.get("bob", old.id) is None
    assert store.get("carl", live.id) is live and store.get("anna", new.id) is new


def test_something_larger_than_the_whole_store_pushes_nobody_out(store):
    kept = store.put("bob", "photo", [part()])
    with pytest.raises(ValueError):
        store.put("anna", "photo", [part(51 * MB)])
    assert store.get("bob", kept.id) is kept


def test_evict_expired_frees_the_memory(store, clock):
    store.put("anna", "photo", [part(100)])
    clock.now += 500
    store.put("anna", "photo", [part(100)])
    clock.now += 500
    assert store.evict_expired() == 1 and store.memory == 100 and len(store) == 1
    clock.now += 500
    assert store.evict_expired() == 1 and store.memory == 0 and len(store) == 0


def test_nothing_shows_in_the_repr(store):
    stored = store.put("anna", "photo", [part()])
    for text in (repr(stored), repr(part())):
        assert "AAAA" not in text and "front camera" not in text
    assert "anna" not in repr(stored) and stored.id not in repr(stored)


def test_parts_are_labelled_and_numbered_across_the_request(store):
    one = store.put("anna", "photo", [part(text="ONE")])
    two = store.put("anna", "bundle", [part(label="a", text="TWO"), part(label="b", text="THREE")])
    assert attachment_parts([one, two]) == [
        {"type": "input_text", "text": "Attachment 1 (photo): front camera"},
        {"type": "input_image", "image_url": "ONE"},
        {"type": "input_text", "text": "Attachment 2 (bundle): a"},
        {"type": "input_image", "image_url": "TWO"},
        {"type": "input_text", "text": "Attachment 2 (bundle): b"},
        {"type": "input_image", "image_url": "THREE"},
    ]


def test_the_summary_for_the_trace_has_no_content(store):
    stored = store.put("anna", "photo", [part()])
    assert stored.summary() == {"kind": "photo", "detail": "1200x800", "size": 100}
