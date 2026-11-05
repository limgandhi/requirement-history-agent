"""T13: when no source is found, say so and let the user pick an alternative via interrupt()."""
from core.qa.qa import _LLMAnswer
from interrupt_helpers import FakeStore, ScriptedModel, run_agent


# ---- T13: not found -> alternatives via interrupt ----------------------------

def test_not_found_offers_choices_and_similar_candidates(tmp_path):
    m = ScriptedModel("ask_history", {"question": "없는 기능"})
    _, tools, seen = run_agent(tmp_path, m, FakeStore(), ["similar"])
    assert seen[0]["kind"] == "choice"
    assert [o["id"] for o in seen[0]["options"]] == ["similar", "rephrase", "cancel"]
    assert "찾지 못했다" in tools[0] and "REQ-003" in tools[0]
    assert m.qa_calls == 0  # nothing retrieved -> no LLM call, nothing invented


def test_not_found_cancel(tmp_path):
    m = ScriptedModel("ask_history", {"question": "없는 기능"})
    _, tools, _ = run_agent(tmp_path, m, FakeStore(), ["cancel"])
    assert "찾지 못했다" in tools[0] and "REQ-" not in tools[0]


def test_not_found_rephrase_then_answered_with_sources(tmp_path):
    ans = _LLMAnswer(answerable=True, answer="최대 7일", used_req_keys=["REQ-003"])
    m = ScriptedModel("ask_history", {"question": "없는 기능"}, qa_answer=ans)
    _, tools, seen = run_agent(tmp_path, m, FakeStore(hits_for=["투표 기간"]), ["rephrase", "투표 기간"])
    assert [p["kind"] for p in seen] == ["choice", "input"]
    assert "최대 7일" in tools[0] and "출처:" in tools[0] and "REQ-003" in tools[0]
    assert m.qa_calls == 1  # replay after each interrupt did not call the LLM again
