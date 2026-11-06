"""T1 smoke test: one real call each to the LLM and the embedding model."""
from src.infrastructure.llm.llm import get_embeddings, get_llm


def test_llm_responds():
    reply = get_llm().invoke("한 단어로만 답해: 안녕")
    assert reply.content


def test_embedding_returns_vector():
    vector = get_embeddings().embed_query("투표 기간은 최대 며칠인가?")
    assert len(vector) == 1024
