"""
Agentic core - framework-agnostic, no FastAPI imports anywhere in here.

Module map (one responsibility per file):

    errors.py        exception hierarchy the app layer maps to HTTP codes
    llm.py           Ollama/OpenAI transport (client factory, chat, model listing)
    prompts.py       every prompt template the agent uses
    extraction.py    raw LLM output -> clean C source (sentinels, repair)
    validation.py    static sanity checks on generated C + requirement detection
    generator.py     generate -> validate -> feedback retry loop
    ports.py         ST-LINK serial port detection / resolution
    builder.py       PlatformIO project writing, compile, flash, archiving
    analysis.py      generated C -> hardware-state JSON (was stm32_parser.py)
    orchestrator.py  the pipeline: retrieve -> generate -> validate -> build -> flash

This package is importable and runnable without the HTTP layer
(`python stm32_agent.py "<command>"`), and without a board attached
(generation only, flash skipped).
"""

from agent.errors import (
    AgentError,
    BuildError,
    ConfigError,
    GenerationError,
    LLMUnavailableError,
)
from agent.orchestrator import AgentOrchestrator, GenerationResult, orchestration_pipeline

__all__ = [
    "AgentError",
    "AgentOrchestrator",
    "BuildError",
    "ConfigError",
    "GenerationError",
    "GenerationResult",
    "LLMUnavailableError",
    "orchestration_pipeline",
]
