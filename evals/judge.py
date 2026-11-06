"""judge.py - LLM-as-judge for the eval run (opt-in with `run_eval.py --judge`).

The keyword/source check in run_eval.py is exact but blind to wording: an answer can contain the
right keyword and still add a claim the evidence does not support. The judge reads the same
evidence the answering LLM saw and rates the answer text on two points:

  supported        : every claim in the answer is backed by the evidence (nothing invented)
  answers_question : the answer actually answers the question that was asked

Rules that keep it honest:
- The judge only rates the answer *text*. Sources (document, version, requester, modifier) are
  built by code from SQLite, so they are checked exactly in run_eval.judge(), never by an LLM.
- The verdict is advisory. It never changes PASS/FAIL or the exit code, because an LLM verdict
  can vary from run to run. A flagged answer is a prompt to read it, not a failure.
- Only answers that found something are judged. "찾지 못했다" answers have no claim to check.
- Same Bedrock model as the answerer (config), temperature 0. A model rating its own output tends
  to be lenient, which is one more reason the verdict is advisory.
"""
from pydantic import BaseModel

from core.qa.qa import _render, _requirement_context

_PROMPT = """너는 요구사항 이력 질의응답의 답변을 평가하는 심사자다. [근거]만 사실로 인정한다.

평가 항목:
- supported: 답변의 모든 내용(값, 숫자, 기간, 사람, 버전, 변경 전후)이 [근거]로 뒷받침되면 true.
  [근거]에 없는 내용이 하나라도 있거나 [근거]와 다르면 false.
- answers_question: 답변이 [질문]에 실제로 답하고 있으면 true. 엉뚱한 요구사항 이야기이거나
  질문이 묻는 것(예: 변경 전후 값, 담당자, 현재 값)을 빠뜨렸으면 false.

reason에는 판단 이유를 한두 문장으로 써라. false이면 어떤 내용이 문제인지 짚어라.

[질문]
{question}

[근거]
{context}

[평가할 답변]
{answer}
"""


class Verdict(BaseModel):
    supported: bool
    answers_question: bool
    reason: str

    @property
    def ok(self) -> bool:
        return self.supported and self.answers_question


def evidence_list(conn, answer) -> list[str]:
    """One text per requirement the answer's sources point at: its content and history rows
    (same rendering the answerer saw)."""
    keys = list(dict.fromkeys(s.req_key for s in answer.sources))
    parts = []
    for key in keys:
        ctx = _requirement_context(conn, key)
        if ctx:
            parts.append(_render(*ctx))
    return parts


def evidence_for(conn, answer) -> str:
    return "\n\n".join(evidence_list(conn, answer))


def judge_answer(llm, question: str, answer_text: str, evidence: str) -> Verdict:
    return llm.with_structured_output(Verdict).invoke(
        _PROMPT.format(question=question, context=evidence, answer=answer_text))
