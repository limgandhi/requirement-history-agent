"""Test-wide settings, applied before any test module imports the app."""
import os

# Tests must not write the LLM / tool call log under data/. test_tracer.py builds its own tracer.
os.environ["TRACE_ENABLED"] = "0"
