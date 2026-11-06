"""LLM-as-judge for evaluation (evaluation/judge.py). Uses a stub LLM, so no Bedrock call."""
from src.core.manual.manual import add_requirement
from src.core.qa.qa import Answer, Source
from src.evaluation.judge import Verdict, evidence_for, judge_answer
from src.infrastructure.db.db import connect, init_schema


class StubLLM:
    def __init__(self, verdict):
        self.verdict, self.prompt = verdict, None

    def with_structured_output(self, schema):
        assert schema is Verdict
        return self

    def invoke(self, prompt):
        self.prompt = prompt
        return self.verdict


def _answer(req_key):
    src = Source(req_key=req_key, title="t", document_name="수동 입력", version=None, requester="a",
                 modifier="b", action="add", source_type="manual", created_at="")
    return Answer(text="공유 링크를 만들 수 있다", found=True, sources=[src])


def test_evidence_has_requirement_and_history_of_the_answer_sources(tmp_path):
    conn = connect(tmp_path / "t.db")
    init_schema(conn)
    key = add_requirement(conn, "투표 공유", "관리자는 투표 링크를 공유할 수 있어야 한다.", "han", "seo")
    other = add_requirement(conn, "다른 기능", "이 내용은 근거에 없어야 한다.", "x", "y")
    text = evidence_for(conn, _answer(key))
    assert "투표 링크를 공유" in text and "han" in text and "seo" in text
    assert other not in text
    conn.close()


def test_judge_prompt_carries_question_evidence_and_answer():
    llm = StubLLM(Verdict(supported=True, answers_question=True, reason="근거와 일치"))
    v = judge_answer(llm, "무엇을 공유하나?", "링크를 공유한다", "EVIDENCE-TEXT")
    assert v.ok
    assert all(t in llm.prompt for t in ("무엇을 공유하나?", "링크를 공유한다", "EVIDENCE-TEXT"))


def test_verdict_is_ok_only_when_both_points_hold():
    assert not Verdict(supported=False, answers_question=True, reason="").ok
    assert not Verdict(supported=True, answers_question=False, reason="").ok


def test_ragas_scorer_returns_scores_and_never_raises(monkeypatch):
    """RagasScorer.score(): per-metric failures become None plus an error text (no Bedrock call)."""
    from src.evaluation import ragas_metrics

    class Metric:
        def __init__(self, value):
            self.value = value

        async def single_turn_ascore(self, sample):
            if isinstance(self.value, Exception):
                raise self.value
            return self.value

    scorer = object.__new__(ragas_metrics.RagasScorer)
    scorer._faithfulness, scorer._relevancy = Metric(1.0), Metric(0.5)
    assert scorer.score("q", "a", ["c"]) == {"faithfulness": 1.0, "answer_relevancy": 0.5}

    scorer._faithfulness = Metric(RuntimeError("throttled"))
    out = scorer.score("q", "a", ["c"])
    assert out["faithfulness"] is None and out["answer_relevancy"] == 0.5 and "throttled" in out["error"]

    scorer._relevancy = Metric(float("nan"))
    assert scorer.score("q", "a", ["c"])["answer_relevancy"] is None
