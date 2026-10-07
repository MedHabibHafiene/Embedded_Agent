"""
Exception hierarchy for the agent.

The app layer (app/main.py) maps each of these to a specific HTTP status so
API consumers get actionable errors instead of a generic 500.
"""


class AgentError(Exception):
    """Base class for all agent failures."""


class LLMUnavailableError(AgentError):
    """The LLM endpoint could not be reached (HTTP 502)."""


class ConfigError(AgentError):
    """Environment/setup problem (missing API key, model not pulled, ...).

    Retrying with the same setup would fail again - surfaced as HTTP 503."""


class GenerationError(AgentError):
    """The LLM could not produce a valid C file after all attempts (HTTP 502)."""


class BuildError(AgentError):
    """Compilation or flashing failed. Carries the full tool output."""

    def __init__(self, message: str, stdout: str = "", stderr: str = ""):
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr

    @property
    def logs(self) -> str:
        return f"{self.stdout}\n{self.stderr}".strip()
