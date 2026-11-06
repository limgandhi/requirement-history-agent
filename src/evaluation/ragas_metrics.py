"""ragas_metrics.py - RAGAS scores for the eval run (opt-in with `run_eval.py --ragas`).

Step 1 scores two reference-free metrics per answer, both 0..1 (higher is better):
  faithfulness     : share of the answer's statements the evidence supports (nothing invented)
  answer_relevancy : how well the answer matches the question (generated-question similarity,
                     uses the Titan embedding model)

The evidence (ragas "retrieved_contexts") is what the answer's sources point at: each
requirement with its history rows, the same text the answering LLM saw. Context precision /
recall are not scored: they need a written reference answer and the retrieved chunks, which the
eval set and ask() do not provide yet.

Like the LLM judge (judge.py) the scores are advisory: they never change PASS/FAIL or the exit
code, and only answers that found something are scored.

Environment notes:
- ragas 0.4.3 imports `langchain_community.chat_models.vertexai`, which the installed
  langchain-community no longer has. A stub module is registered before the import (only the
  class name is needed; Vertex is never used).
- ragas sends anonymous usage data unless RAGAS_DO_NOT_TRACK is set. It is set here, because
  requirement text must not leave through anything but Bedrock.
- ragas needs a LangChain chat model, so it gets the plain Sonnet model, without the Haiku
  fallback that get_llm() adds. A throttled call is retried by ragas, then reported as an error.
"""
import asyncio
import os
import sys
import types

os.environ.setdefault("RAGAS_DO_NOT_TRACK", "true")
if "langchain_community.chat_models.vertexai" not in sys.modules:
    _stub = types.ModuleType("langchain_community.chat_models.vertexai")
    _stub.ChatVertexAI = type("ChatVertexAI", (), {})
    sys.modules["langchain_community.chat_models.vertexai"] = _stub

from ragas import SingleTurnSample  # noqa: E402
from ragas.embeddings import LangchainEmbeddingsWrapper  # noqa: E402
from ragas.llms import LangchainLLMWrapper  # noqa: E402
from ragas.metrics import Faithfulness, ResponseRelevancy  # noqa: E402

from config import config  # noqa: E402
from src.infrastructure.llm.llm import get_embeddings, get_single_llm  # noqa: E402

METRIC_NAMES = ("faithfulness", "answer_relevancy")


class RagasScorer:
    def __init__(self):
        llm = LangchainLLMWrapper(get_single_llm(config.LLM_MODEL_ID))
        self._faithfulness = Faithfulness(llm=llm)
        self._relevancy = ResponseRelevancy(llm=llm, embeddings=LangchainEmbeddingsWrapper(get_embeddings()))

    def score(self, question: str, answer_text: str, contexts: list[str]) -> dict:
        """{"faithfulness": float|None, "answer_relevancy": float|None, "error"?: str}. Never raises."""
        sample = SingleTurnSample(user_input=question, response=answer_text, retrieved_contexts=contexts)
        out: dict = {}
        for name, metric in (("faithfulness", self._faithfulness), ("answer_relevancy", self._relevancy)):
            try:
                value = float(asyncio.run(metric.single_turn_ascore(sample)))
                out[name] = None if value != value else round(value, 3)  # NaN -> None
            except Exception as e:
                out[name] = None
                out.setdefault("error", f"{name}: {type(e).__name__}: {e}")
        return out
