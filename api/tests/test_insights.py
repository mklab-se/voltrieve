import asyncio

import pytest

from app.insights import (
    LiveFeed,
    TurnRecorder,
    estimate_cost_sek,
    mask_prompt,
    pseudonym,
)
from voltrieve_agent.events import AgentEvent


def test_pseudonym_is_stable_short_and_hides_the_id():
    oid = "11111111-2222-3333-4444-555555555555"
    assert pseudonym(oid) == pseudonym(oid)
    assert pseudonym(oid) != pseudonym("other")
    assert len(pseudonym(oid)) == 12 and oid[:8] not in pseudonym(oid)


def test_mask_prompt_hides_contact_details_and_identity_numbers():
    text = "Mail anna.svensson@example.com or call +46 70 123 45 67, personnummer 19850412-1234."
    masked = mask_prompt(text)
    assert "anna.svensson" not in masked and "123 45 67" not in masked and "19850412" not in masked
    assert "[email]" in masked and "[phone]" in masked and "[id number]" in masked
    assert mask_prompt("What does the Fjord Plus AWD cost?") == "What does the Fjord Plus AWD cost?"


def test_mask_prompt_is_cut_to_a_readable_length():
    assert len(mask_prompt("x" * 1000)) <= 241


def test_cost_estimate_uses_the_price_table():
    usage = {"input_tokens": 1_000_000, "output_tokens": 100_000,
             "input_tokens_details": {"cached_tokens": 200_000}}
    # 0.8 M uncached at 1.00, 0.2 M cached at 0.05, 0.1 M output at 5.00 USD, then to SEK
    from app.insights import USD_TO_SEK

    assert estimate_cost_sek("gpt-6.1-terra", usage) == pytest.approx((0.8 + 0.01 + 0.5) * USD_TO_SEK)
    assert estimate_cost_sek("unknown-model", usage) is None


def _trace(step, **data):
    return AgentEvent("trace", {"step": step, **data})


def test_recorder_summarises_a_loop_turn():
    clock = iter([0.0, 0.2, 1.5, 3.0])  # start, first status, first text, finish
    rec = TurnRecorder("loop", "gpt-6.1-terra", "u1", clock=lambda: next(clock))
    rec.observe(AgentEvent("status", {"key": "thinking"}))
    rec.observe(_trace("model_called"))
    rec.observe(_trace("server_tool_called", tool="search_voltrieve_docs"))
    rec.observe(_trace("knowledge_base_retrieved", knowledge_base="voltrieve-kb", search_reasoning_tokens=40,
                       planning_input_tokens=100, planning_output_tokens=10))
    rec.observe(AgentEvent("delta", {"text": "Hi"}))
    rec.observe(AgentEvent("done", {"usage": {"input_tokens": 1000, "output_tokens": 50}}))
    s = rec.finish()
    assert s.outcome == "answered"
    assert s.first_status_ms == 200 and s.first_text_ms == 1500 and s.duration_ms == 3000
    assert s.tools == ["search_voltrieve_docs"] and s.knowledge_bases == ["voltrieve-kb"]
    assert s.model_calls == 1 and s.input_tokens == 1000 and s.output_tokens == 50
    assert s.search_reasoning_tokens == 40 and s.cost_sek is not None and s.cost_sek > 0


def test_recorder_reads_mcp_searches_curated_answers_client_rounds_and_errors():
    rec = TurnRecorder("prompt", "gpt-6.1-terra", "u1")
    rec.observe(_trace("mcp_tool_called", server="voltrieve-parts-kb"))
    rec.observe(_trace("final_answer_sent", tool="get_curated_answer"))
    assert rec.finish().outcome == "curated" and rec.finish().knowledge_bases == ["voltrieve-parts-kb"]
    paused = TurnRecorder("loop", "gpt-6.1-terra", "u1")
    paused.observe(AgentEvent("client_tool_calls", {"calls": []}))
    assert paused.finish().outcome == "client_tool"
    failed = TurnRecorder("hosted", "gpt-6.1-terra", "u1")
    failed.observe(AgentEvent("error", {"code": "search_quota"}))
    assert (failed.finish().outcome, failed.finish().error_code) == ("error", "search_quota")


def test_span_attributes_use_genai_names_and_carry_no_text():
    rec = TurnRecorder("loop", "gpt-6.1-terra", "abc")
    rec.observe(AgentEvent("delta", {"text": "secret answer"}))
    rec.observe(AgentEvent("done", {"usage": {"input_tokens": 10, "output_tokens": 2}}))
    attrs = rec.finish().span_attributes()
    assert attrs["gen_ai.request.model"] == "gpt-6.1-terra"
    assert attrs["gen_ai.usage.input_tokens"] == 10 and attrs["gen_ai.usage.output_tokens"] == 2
    assert attrs["voltrieve.backend"] == "loop" and attrs["voltrieve.user"] == "abc"
    assert "secret answer" not in str(attrs)


async def test_live_feed_streams_new_entries_and_hides_prompts_unless_asked():
    feed = LiveFeed(size=3)
    hidden, shown = feed.subscribe(show_prompts=False), feed.subscribe(show_prompts=True)
    feed.publish({"turn_id": "t1", "user": "u1", "prompt": "hello"})
    assert (await asyncio.wait_for(hidden.get(), 1))["prompt"] is None
    assert (await asyncio.wait_for(shown.get(), 1))["prompt"] == "hello"
    for n in range(5):
        feed.publish({"turn_id": f"t{n + 2}", "user": f"u{n}", "prompt": "p"})
    assert [e["turn_id"] for e in feed.recent(show_prompts=False)] == ["t4", "t5", "t6"]
    assert all(e["prompt"] is None for e in feed.recent(show_prompts=False))


def test_live_feed_counts_active_users_in_a_window():
    now = [1000.0]
    feed = LiveFeed(clock=lambda: now[0])
    feed.publish({"turn_id": "a", "user": "u1"})
    feed.publish({"turn_id": "b", "user": "u2"})
    feed.publish({"turn_id": "c", "user": "u1"})
    assert feed.active_users(window=300) == 2
    now[0] += 301
    assert feed.active_users(window=300) == 0
