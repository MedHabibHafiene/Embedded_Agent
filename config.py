"""
Central configuration - the single source of truth for every layer.

All values can be overridden through the environment (a local `.env` file is
loaded via python-dotenv). Consumers:

  - app/   reads the API_* / CORS_ORIGINS settings
  - agent/ reads the OLLAMA_* and STM32 build/flash settings
  - rag/   reads the RAG_* settings

Note: a missing OLLAMA_API_KEY no longer crashes at import time (it used to,
which broke RAG-only commands like `python -m rag.ingest`). The check is done
where the key is actually needed, in agent.llm.create_client().
"""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent

load_dotenv(PROJECT_ROOT / ".env")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_list(name: str, default: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


# --------------------------------------------------------------
# API server (app/)
# --------------------------------------------------------------
API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = _env_int("API_PORT", 8000)
CORS_ORIGINS = _env_list(
    "CORS_ORIGINS",
    "http://localhost:3000,http://localhost:5173,"
    "http://127.0.0.1:3000,http://127.0.0.1:5173",
)

# --------------------------------------------------------------
# LLM (Ollama, OpenAI-compatible endpoint)
# --------------------------------------------------------------
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY")

# Local daemon by default; https://ollama.com/v1 for the direct cloud API.
OLLAMA_BASE_URL = os.getenv("BASE_URL", "http://localhost:11434/v1")

# Which model to ask Ollama for, e.g. "llama3.1" or a cloud model.
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1")

LLM_MAX_TOKENS = _env_int("LLM_MAX_TOKENS", 2500)

# How many generate -> validate -> feedback retries before giving up.
LLM_MAX_ATTEMPTS = _env_int("LLM_MAX_ATTEMPTS", 3)

# Transport-level tuning: generation can legitimately take minutes, but a
# dead Ollama server should fail fast - the generator already does its own
# higher-level retries, so keep the client's own retry count low.
LLM_TIMEOUT_SEC = _env_int("LLM_TIMEOUT_SEC", 300)
LLM_MAX_TRANSPORT_RETRIES = _env_int("LLM_MAX_TRANSPORT_RETRIES", 1)

# --------------------------------------------------------------
# RAG (rag/)
# --------------------------------------------------------------
RAG_DB_DIR = PROJECT_ROOT / os.getenv("RAG_DB_DIR", "chroma_db")
DOCS_DIR = PROJECT_ROOT / os.getenv("DOCS_DIR", "documentation")
RAG_COLLECTION = "stm32_docs"
RAG_TOP_K = _env_int("RAG_TOP_K", 5)
RAG_CHUNK_MAX_CHARS = _env_int("RAG_CHUNK_MAX_CHARS", 1000)

# Vector store backend: "cloud" (Chroma Cloud) or "local" (./chroma_db).
# Cloud needs CHROMA_API_KEY / CHROMA_TENANT / CHROMA_DATABASE; falling back
# to "local" only requires switching RAG_BACKEND in .env.
RAG_BACKEND = os.getenv("RAG_BACKEND", "local").strip().lower()
CHROMA_API_KEY = os.getenv("CHROMA_API_KEY")
CHROMA_TENANT = os.getenv("CHROMA_TENANT")
CHROMA_DATABASE = os.getenv("CHROMA_DATABASE")

# Scraped corpus, written by `python -m scraping.run` and indexed by
# rag.ingest alongside DOCS_DIR. Scraping itself never runs automatically.
SCRAPED_DIR = PROJECT_ROOT / os.getenv("SCRAPED_DIR", "scraping/output")

# --------------------------------------------------------------
# Chat mode (agent/chat.py)
# --------------------------------------------------------------
# Chat answers from a wider retrieval net than code generation and is
# allowed a more conversational temperature.
CHAT_TOP_K = _env_int("CHAT_TOP_K", 8)
CHAT_MAX_TOKENS = _env_int("CHAT_MAX_TOKENS", 1200)
CHAT_TEMPERATURE = float(os.getenv("CHAT_TEMPERATURE", "0.3"))

# --------------------------------------------------------------
# Build & flash (agent/builder.py)
# --------------------------------------------------------------
STM32_PROJECT_DIR = PROJECT_ROOT / "stm32_project"
BUILD_TIMEOUT_SEC = _env_int("BUILD_TIMEOUT_SEC", 120)
UPLOAD_TIMEOUT_SEC = _env_int("UPLOAD_TIMEOUT_SEC", 60)

# PlatformIO upload port. Resolution order in agent/ports.py:
#   env var > this value > auto-detect (pyserial) > platform fallback.
PLATFORMIO_UPLOAD_PORT = os.getenv("PLATFORMIO_UPLOAD_PORT")

UPLOAD_PORT_FALLBACK_WIN = "COM5"
UPLOAD_PORT_FALLBACK_POSIX = "/dev/ttyACM0"

# Where raw LLM attempts are dumped when generation keeps failing.
FAILED_GENERATIONS_DIR = PROJECT_ROOT / "failed_generations"

# --------------------------------------------------------------
# Chat log persistence (MongoDB, app/chatlog.py)
# --------------------------------------------------------------
# Full driver connection string, e.g.
#   mongodb+srv://user:pass@cluster0.ujwdilp.mongodb.net
# When unset (or the server is unreachable at startup) the chat falls back
# to stateless mode: the client just resends its history, as before.
MONGODB_URI = os.getenv("MONGODB_URI")
MONGODB_DB = os.getenv("MONGODB_DB", "embedded_agent")

# --------------------------------------------------------------
# Job store (app/jobs.py)
# --------------------------------------------------------------
JOBS_DIR = PROJECT_ROOT / os.getenv("JOBS_DIR", "data/jobs")
