"""Bedrock LLM / embedding factories, with Haiku as the fallback LLM (T14).

Fallback rule (decided in T14):
- The default LLM (Sonnet) is tried up to 1 + PRIMARY_RETRIES times. boto3 already
  retries throttling with backoff inside each try.
- Any exception from those tries (throttling, timeout, service error, ...) switches
  that one call to the fallback LLM (Haiku). The next call starts with Sonnet again.
- A switch is logged and counted; fallback_count() lets a front end tell the user.
- If the fallback fails too, its exception is raised.
"""
import logging
from langchain_aws import BedrockEmbeddings, ChatBedrockConverse

from config import config


log = logging.getLogger(__name__)

PRIMARY_RETRIES = 1  # extra tries on the default LLM before switching
_fallback_count = 0


def fallback_count() -> int:
    """How many calls in this process were answered by the fallback LLM."""
    return _fallback_count


class _Pair:
    """A primary/fallback pair of runnables with the same configuration."""

    def __init__(self, primary, fallback, retries: int):
        self.primary, self.fallback, self.retries = primary, fallback, retries

    def invoke(self, value, *args, **kwargs):
        global _fallback_count
        error = None
        for _ in range(1 + self.retries):
            try:
                return self.primary.invoke(value, *args, **kwargs)
            except Exception as e:  # any failure of the default LLM
                error = e
        _fallback_count += 1
        log.warning("default LLM failed (%s: %s); using fallback LLM", type(error).__name__, error)
        return self.fallback.invoke(value, *args, **kwargs)

    def bind_tools(self, *args, **kwargs):
        return _Pair(self.primary.bind_tools(*args, **kwargs),
                     self.fallback.bind_tools(*args, **kwargs), self.retries)

    def with_structured_output(self, *args, **kwargs):
        return _Pair(self.primary.with_structured_output(*args, **kwargs),
                     self.fallback.with_structured_output(*args, **kwargs), self.retries)


class FallbackLLM(_Pair):
    """Chat model that falls back from Sonnet to Haiku; used like a normal chat model."""


def get_single_llm(model_id: str) -> ChatBedrockConverse:
    return ChatBedrockConverse(
        model=model_id,
        region_name=config.AWS_REGION,
        temperature=config.LLM_TEMPERATURE,
    )


def get_llm() -> FallbackLLM:
    return FallbackLLM(
        get_single_llm(config.LLM_MODEL_ID),
        get_single_llm(config.FALLBACK_LLM_MODEL_ID),
        PRIMARY_RETRIES,
    )


def get_embeddings() -> BedrockEmbeddings:
    return BedrockEmbeddings(
        model_id=config.EMBEDDING_MODEL_ID,
        region_name=config.AWS_REGION,
        model_kwargs={"dimensions": config.EMBEDDING_DIMENSIONS},
    )
