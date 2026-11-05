"""LLM / tool call log as JSON Lines (one record per line), written by a LangChain callback.

Based on practices/day7_practice/local_tracer.py. Pass the tracer in the run config
(`{"callbacks": [tracer]}`); LangGraph hands it to every model and tool call inside the run,
including the model calls a tool makes itself (question answering, extraction). Records:

  llm_start  / llm_end / llm_error     model, prompt messages, answer, tool calls, tokens, seconds
  tool_start / tool_end / tool_error   tool name, input, output, seconds
  tool_interrupt                       the tool paused to ask the user (interrupt()), not an error

Each record carries the conversation (`thread_id`), `run_id` and `parent_run_id`. The `model`
field shows which model answered, so a switch to the fallback model is visible in the log.
Long texts are cut to TEXT_LIMIT characters. The file holds requirement text, so it lives under
data/ (not committed). A logging problem never breaks a request.
"""
import json
import logging
import threading
import time
from pathlib import Path

from langchain_core.callbacks import BaseCallbackHandler
from langgraph.errors import GraphBubbleUp

from config import config

log = logging.getLogger(__name__)

TEXT_LIMIT = 1000


def get_text(message) -> str:
    """Chat models may return content as a list of blocks; keep only the text."""
    content = getattr(message, "content", message)
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict))
    return content if isinstance(content, str) else str(content)


def _cut(text, limit: int = TEXT_LIMIT) -> str:
    text = text if isinstance(text, str) else str(text)
    return text if len(text) <= limit else text[:limit] + f"...(+{len(text) - limit}자)"


class FileTracer(BaseCallbackHandler):
    """Appends one JSON line per LLM / tool event to `path`."""

    def __init__(self, path: str | Path = config.TRACE_LOG_PATH):
        self.path = Path(path)
        self._starts: dict = {}  # run_id -> (start time, model)
        self._lock = threading.Lock()  # web requests run in threads: keep each line whole

    def _write(self, record: dict) -> None:
        record["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(line)
        except Exception as e:  # logging must never break the answer
            log.warning("trace log not written (%s: %s)", type(e).__name__, e)

    def _begin(self, run_id, model=None) -> None:
        with self._lock:
            self._starts[run_id] = (time.time(), model)

    def _end(self, run_id) -> tuple[float, str | None]:
        with self._lock:
            started, model = self._starts.pop(run_id, (time.time(), None))
        return round(time.time() - started, 2), model

    @staticmethod
    def _base(event, run_id, parent_run_id, metadata) -> dict:
        record = {"event": event, "run_id": str(run_id)}
        if parent_run_id:
            record["parent_run_id"] = str(parent_run_id)
        if metadata and metadata.get("thread_id"):
            record["thread_id"] = metadata["thread_id"]
        return record

    # ---- LLM ----
    def on_chat_model_start(self, serialized, messages, *, run_id, parent_run_id=None, metadata=None, **kw):
        model = (metadata or {}).get("ls_model_name")
        self._begin(run_id, model)
        record = self._base("llm_start", run_id, parent_run_id, metadata)
        record["model"] = model
        record["input"] = [f"{getattr(m, 'type', '?')}: {_cut(get_text(m))}" for batch in messages for m in batch]
        self._write(record)

    def on_llm_end(self, response, *, run_id, parent_run_id=None, **kw):
        latency, model = self._end(run_id)
        generation = response.generations[0][0]
        message = getattr(generation, "message", None)
        record = self._base("llm_end", run_id, parent_run_id, None)
        record["model"] = model
        record["output"] = _cut(generation.text)
        record["latency_s"] = latency
        if message is not None:
            calls = getattr(message, "tool_calls", None)
            if calls:  # a tool choice, or a structured answer (which the model returns as a tool call)
                record["tool_calls"] = [{"name": c.get("name"), "args": _cut(json.dumps(c.get("args"), ensure_ascii=False, default=str), 500)} for c in calls]
            usage = getattr(message, "usage_metadata", None)
            if usage:
                record["usage"] = dict(usage)
        self._write(record)

    def on_llm_error(self, error, *, run_id, parent_run_id=None, **kw):
        latency, model = self._end(run_id)
        record = self._base("llm_error", run_id, parent_run_id, None)
        record.update(model=model, latency_s=latency, error=f"{type(error).__name__}: {_cut(str(error), 500)}")
        self._write(record)

    # ---- tools ----
    def on_tool_start(self, serialized, input_str, *, run_id, parent_run_id=None, metadata=None, **kw):
        self._begin(run_id)
        record = self._base("tool_start", run_id, parent_run_id, metadata)
        record["tool"] = (serialized or {}).get("name") or kw.get("name")
        record["input"] = _cut(input_str)
        self._write(record)

    def on_tool_end(self, output, *, run_id, parent_run_id=None, **kw):
        latency, _ = self._end(run_id)
        record = self._base("tool_end", run_id, parent_run_id, None)
        record["output"] = _cut(get_text(output))
        record["latency_s"] = latency
        self._write(record)

    def on_tool_error(self, error, *, run_id, parent_run_id=None, **kw):
        latency, _ = self._end(run_id)
        interrupted = isinstance(error, GraphBubbleUp)  # interrupt(): waiting for the user, not a failure
        record = self._base("tool_interrupt" if interrupted else "tool_error", run_id, parent_run_id, None)
        record["latency_s"] = latency
        if not interrupted:
            record["error"] = f"{type(error).__name__}: {_cut(str(error), 500)}"
        self._write(record)


_tracer: FileTracer | None = None


def get_tracer() -> FileTracer | None:
    """The shared tracer, or None when tracing is switched off (TRACE_ENABLED=0 in .env)."""
    global _tracer
    if not config.TRACE_ENABLED:
        return None
    if _tracer is None:
        _tracer = FileTracer(config.TRACE_LOG_PATH)
    return _tracer
