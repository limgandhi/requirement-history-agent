"""T14: fall back from Sonnet to Haiku when the default LLM call fails (no Bedrock)."""
import logging

import pytest

from src.infrastructure.llm import llm as llm_mod
from src.infrastructure.llm.llm import FallbackLLM, fallback_count


class Model:
    def __init__(self, name, fail_times=0):
        self.name, self.fail_times, self.calls = name, fail_times, 0

    def invoke(self, value, *a, **k):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError(f"{self.name} down")
        return f"{self.name}:{value}"

    def bind_tools(self, tools):
        return self

    def with_structured_output(self, schema):
        return self


def test_default_model_used_when_healthy():
    p, f = Model("sonnet"), Model("haiku")
    before = fallback_count()
    assert FallbackLLM(p, f, 1).invoke("q") == "sonnet:q"
    assert f.calls == 0 and fallback_count() == before


def test_retries_default_model_once_before_falling_back():
    p, f = Model("sonnet", fail_times=1), Model("haiku")
    assert FallbackLLM(p, f, 1).invoke("q") == "sonnet:q"  # second try succeeded
    assert p.calls == 2 and f.calls == 0


def test_forced_failure_answers_with_haiku_and_is_recorded(caplog):
    p, f = Model("sonnet", fail_times=99), Model("haiku")
    before = fallback_count()
    with caplog.at_level(logging.WARNING):
        out = FallbackLLM(p, f, 1).invoke("q")
    assert out == "haiku:q" and p.calls == 2 and f.calls == 1
    assert fallback_count() == before + 1
    assert "fallback" in caplog.text


def test_bound_and_structured_variants_also_fall_back():
    p, f = Model("sonnet", fail_times=99), Model("haiku")
    llm = FallbackLLM(p, f, 0)
    assert llm.bind_tools([]).invoke("a") == "haiku:a"
    assert llm.with_structured_output(dict).invoke("b") == "haiku:b"


def test_both_fail_raises():
    p, f = Model("sonnet", fail_times=99), Model("haiku", fail_times=99)
    with pytest.raises(RuntimeError, match="haiku down"):
        FallbackLLM(p, f, 0).invoke("q")


def test_get_llm_builds_sonnet_primary_and_haiku_fallback():
    from config import config
    llm = llm_mod.get_llm()
    assert llm.primary.model_id == config.LLM_MODEL_ID
    assert llm.fallback.model_id == config.FALLBACK_LLM_MODEL_ID
