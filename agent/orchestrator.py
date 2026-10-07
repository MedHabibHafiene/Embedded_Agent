"""
Orchestrator: the agent pipeline tying rag/ and agent/ modules together.

Two clearly separated entry points, mirroring the two phases of the workflow:

  AgentOrchestrator.generate(command)  - pure: retrieve context, generate,
        validate, parse hardware state. No filesystem writes, no hardware
        access. Safe to expose directly over the API.
  AgentOrchestrator.flash(code, ...)   - the hardware-touching phase: write
        to the PlatformIO project, compile, upload, archive.

`run()` chains both for CLI use, keeping the interactive confirm gate before
anything touches the board. `orchestration_pipeline` is kept as a thin
backwards-compatible wrapper around run().
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path

from agent import builder
from agent.analysis import parse_stm32_code
from agent.errors import BuildError, ConfigError, GenerationError
from agent.generator import generate_firmware
from agent.llm import create_client
from agent.prompts import CODE_PROMPT_TEMPLATE, FEEDBACK_TEMPLATE, SYSTEM_PROMPT
from agent.validation import validate_c_source
from config import OLLAMA_MODEL, STM32_PROJECT_DIR
from rag import Retriever

log = logging.getLogger(__name__)


@dataclass
class GenerationResult:
    """Outcome of the pure generation phase."""

    command: str
    c_code: str
    hardware_state: dict
    context: str
    sources: list[str] = field(default_factory=list)
    model: str = OLLAMA_MODEL
    build_status: str | None = None   # "success" | "error" | None (not checked)
    build_logs: str | None = None

    def as_dict(self) -> dict:
        return {
            "status": "success",
            "c_code": self.c_code,
            "hardware_state": self.hardware_state,
            "context": self.context,
            "sources": self.sources,
            "model": self.model,
            "build_status": self.build_status,
            "build_logs": self.build_logs,
        }


class AgentOrchestrator:
    """Coordinates RAG retrieval, LLM generation, validation and flashing."""

    def __init__(self, client=None, retriever: Retriever | None = None):
        # Both are lazy: the orchestrator can be constructed at server startup
        # even when Ollama is down (errors surface on first use, mapped to
        # HTTP 503 by the app layer).
        self._client = client
        self._retriever = retriever

    @property
    def client(self):
        if self._client is None:
            self._client = create_client()  # raises ConfigError if unset up
        return self._client

    @property
    def retriever(self) -> Retriever:
        if self._retriever is None:
            self._retriever = Retriever()
        return self._retriever

    # ------------------------------------------------------------ phase 1
    def generate(self, command: str) -> GenerationResult:
        """RAG context -> firmware C code -> validated & parsed. Pure."""
        log.info("Retrieving hardware context for: %r", command)
        retrieval = self.retriever.retrieve(command)
        log.info("Hardware context:\n%s", retrieval.text or "<none>")

        log.info("Generating firmware C code...")
        code = generate_firmware(self.client, command, retrieval.text)
        log.info("Code generation finished. Extracted %d characters.", len(code))

        return GenerationResult(
            command=command,
            c_code=code,
            hardware_state=parse_stm32_code(code),
            context=retrieval.text,
            sources=retrieval.sources,
        )

    # ------------------------------------------------------------ phase 2
    def flash(
        self,
        code: str,
        command: str = "",
        project_dir: Path = STM32_PROJECT_DIR,
        port: str | None = None,
    ) -> builder.BuildOutcome:
        """Write generated code, compile, upload to the board, archive."""
        project_dir.mkdir(parents=True, exist_ok=True)
        return builder.flash_firmware(project_dir, code, command=command, port=port)

    def verify(
        self,
        code: str,
        command: str = "",
        project_dir: Path = STM32_PROJECT_DIR,
    ) -> builder.BuildOutcome:
        """Compile-check generated code without touching any hardware."""
        project_dir.mkdir(parents=True, exist_ok=True)
        return builder.verify_firmware(project_dir, code, command=command)

    # ------------------------------------------------- self-testing loop
    def generate_verified(self, command: str, max_compile_fixes: int = 1) -> GenerationResult:
        """Generate, then compile-check the result - WITHOUT any flashing.

        The best test of generated firmware is the compiler itself, so the
        accepted candidate is built with `pio run` (upload never runs). If
        the compiler rejects it, its errors are fed back to the model for
        one corrective regeneration, then checked again. The result carries
        the final build outcome.
        """
        result = self.generate(command)

        for fix_round in range(max_compile_fixes + 1):
            try:
                outcome = self.verify(result.c_code, command=command)
            except BuildError as e:
                result.build_status = "error"
                result.build_logs = e.logs
                if fix_round >= max_compile_fixes:
                    log.warning(
                        "Compile check failed after %d fix round(s): keeping "
                        "the generated code and reporting the compiler output.",
                        fix_round,
                    )
                    return result
                log.info("Compile check failed - feeding errors back to the model.")
                result = self._regenerate_with_compile_errors(command, result, e.logs)
                continue

            result.build_status = "success"
            result.build_logs = outcome.logs
            log.info("Compile check passed - firmware builds cleanly (no flash attempted).")
            return result

        return result  # unreachable, keeps type-checkers content

    def _regenerate_with_compile_errors(
        self, command: str, previous: GenerationResult, compile_logs: str
    ) -> GenerationResult:
        """One corrective LLM round with the compiler output in the loop."""
        from agent import llm
        from agent.extraction import clean_generated_code

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": CODE_PROMPT_TEMPLATE.format(command=command, context=previous.context),
            },
            {"role": "assistant", "content": previous.c_code},
            {
                "role": "user",
                "content": FEEDBACK_TEMPLATE.format(
                    problems=(
                        "The firmware failed to compile. Compiler output:\n"
                        f"{compile_logs[-3000:]}"
                    )
                ),
            },
        ]
        code = clean_generated_code(llm.chat(self.client, messages))
        problems = validate_c_source(code) if code else ["empty result after extraction"]
        if problems:
            raise GenerationError(
                "Corrective regeneration after compile errors produced invalid C:\n"
                + "\n".join(f"  - {p}" for p in problems)
            )
        log.info("Corrective regeneration passed static validation.")
        return GenerationResult(
            command=command,
            c_code=code,
            hardware_state=parse_stm32_code(code),
            context=previous.context,
            sources=previous.sources,
        )

    # ------------------------------------------------------------ full run
    def run(
        self,
        command: str,
        auto_confirm: bool = False,
        project_dir: Path = STM32_PROJECT_DIR,
        port: str | None = None,
    ) -> dict:
        """Full CLI pipeline: generate, build, (confirm,) flash, archive."""
        result = self.generate(command)

        project_dir.mkdir(parents=True, exist_ok=True)
        builder.ensure_platformio_ini(project_dir, port=port)
        builder.write_source_files(project_dir, result.c_code)

        log.info("Starting build & flash pipeline...")
        builder.build_and_flash(
            project_dir, auto_confirm=auto_confirm, command=command, port=port
        )
        return result.as_dict()


# Backwards-compatible module-level entry point (old callers used
# orchestration_pipeline(command, ...)). New code should prefer
# AgentOrchestrator directly.
def orchestration_pipeline(
    command: str,
    project_dir: Path = STM32_PROJECT_DIR,
    client=None,
    retriever: Retriever | None = None,
    auto_confirm: bool = False,
    port: str | None = None,
) -> dict:
    return AgentOrchestrator(client=client, retriever=retriever).run(
        command, auto_confirm=auto_confirm, project_dir=project_dir, port=port
    )
