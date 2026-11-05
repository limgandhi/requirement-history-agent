"""Central settings. Credentials come from .env (never hard-coded)."""
import os

from dotenv import load_dotenv

load_dotenv()

AWS_REGION = "us-east-1"

LLM_MODEL_ID = "global.anthropic.claude-sonnet-4-5-20250929-v1:0"
FALLBACK_LLM_MODEL_ID = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
EMBEDDING_MODEL_ID = "amazon.titan-embed-text-v2:0"
EMBEDDING_DIMENSIONS = 1024

LLM_TEMPERATURE = 0

CHROMA_DIR = "data/chroma_db"
CHROMA_COLLECTION = "requirements_history"

# LLM / tool call log (JSON Lines). Set TRACE_ENABLED=0 in .env to switch it off.
TRACE_LOG_PATH = "data/logs/llm_trace.jsonl"
TRACE_ENABLED = os.getenv("TRACE_ENABLED", "1") != "0"
