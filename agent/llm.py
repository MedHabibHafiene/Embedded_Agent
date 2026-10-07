"""
LLM transport - the only module that talks to Ollama.

Knows about OpenAI client construction, chat completion calls, and diagnosing
model-availability problems. Knows nothing about prompts or validation policy.
"""

import json
import logging
import urllib.request

import openai

from agent.errors import ConfigError, LLMUnavailableError
from config import (
    LLM_MAX_TOKENS,
    LLM_MAX_TRANSPORT_RETRIES,
    LLM_TIMEOUT_SEC,
    OLLAMA_API_KEY,
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
)

log = logging.getLogger(__name__)


def create_client() -> openai.OpenAI:
    """Build the OpenAI-compatible client against the configured Ollama endpoint."""
    api_key = OLLAMA_API_KEY
    if not api_key:
        raise ConfigError(
            "OLLAMA_API_KEY environment variable not set. "
            "Add it to the .env file in the project root."
        )
    return openai.OpenAI(
        base_url=OLLAMA_BASE_URL,
        api_key=api_key,
        timeout=LLM_TIMEOUT_SEC,
        max_retries=LLM_MAX_TRANSPORT_RETRIES,
    )


def list_available_models(base_url: str = OLLAMA_BASE_URL) -> list[str]:
    """Query Ollama's native /api/tags endpoint (not the OpenAI-compat path)
    to list locally pulled models, for a more actionable error message."""
    tags_url = base_url.rstrip("/")
    if tags_url.endswith("/v1"):
        tags_url = tags_url[: -len("/v1")]
    tags_url += "/api/tags"
    try:
        with urllib.request.urlopen(tags_url, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return [m.get("name", "?") for m in data.get("models", [])]
    except Exception:
        return []


def chat(
    client: openai.OpenAI,
    messages: list[dict],
    model: str = OLLAMA_MODEL,
    temperature: float = 0.1,
    max_tokens: int = LLM_MAX_TOKENS,
) -> str:
    """One chat completion. Returns the assistant message content.

    - Model-not-pulled is a setup problem: raises ConfigError (with a pull
      hint) instead of letting the caller retry the same missing model.
    - Server-unreachable is an upstream problem: raises LLMUnavailableError
      so the API can answer with 502 instead of a raw connection traceback.
    """
    try:
        completion = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            top_p=1,
            max_tokens=max_tokens,
            stream=False,
        )
    except openai.APIConnectionError as e:
        raise LLMUnavailableError(
            f"Could not reach the Ollama server at {client.base_url}. "
            "Is it running (`ollama serve`)?"
        ) from e
    except openai.NotFoundError as e:
        available = list_available_models(str(client.base_url))
        hint = (
            f"Models available on this Ollama server: {', '.join(available)}"
            if available
            else "Could not reach the Ollama server to list models - "
            "is it running (`ollama serve`)?"
        )
        raise ConfigError(
            f"Model '{model}' is not available at {client.base_url}.\n"
            f"{hint}\n"
            f"Pull it with: ollama pull {model}\n"
            f"Or point at an installed model with: set OLLAMA_MODEL=<name> "
            f"(PowerShell: $env:OLLAMA_MODEL=\"<name>\")"
        ) from e

    content = (completion.choices[0].message.content or "").strip()
    log.debug("LLM replied with %d character(s).", len(content))
    return content
