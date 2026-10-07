"""
Generator: the generate -> extract -> validate -> feedback retry loop.

Policy lives here (temperature schedule, attempt cap, corrective feedback);
transport lives in llm.py, extraction/repair in extraction.py, the checks in
validation.py. On total failure the raw attempts are dumped to
failed_generations/ for post-mortem before a GenerationError is raised.
"""

import logging
from pathlib import Path

import openai

from agent import llm
from agent.errors import GenerationError
from agent.extraction import clean_generated_code
from agent.prompts import CODE_PROMPT_TEMPLATE, FEEDBACK_TEMPLATE, SYSTEM_PROMPT
from agent.validation import detect_requested_features, validate_c_source
from config import FAILED_GENERATIONS_DIR, LLM_MAX_ATTEMPTS, OLLAMA_MODEL

log = logging.getLogger(__name__)


def generate_firmware(
    client: openai.OpenAI,
    command: str,
    context: str,
    model: str = OLLAMA_MODEL,
    max_attempts: int = LLM_MAX_ATTEMPTS,
) -> str:
    """Generate a validated firmware C file for `command`.

    Retries with the failure reasons appended to the conversation so the next
    attempt is corrective, not another independent roll of the dice.
    """
    required_features = detect_requested_features(command)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": CODE_PROMPT_TEMPLATE.format(command=command, context=context)},
    ]

    raw_attempts = []
    last_problems: list[str] = []

    for attempt in range(1, max_attempts + 1):
        # Lower the temperature on each retry to push toward more
        # deterministic, instruction-following output.
        temperature = max(0.0, 0.1 - 0.05 * (attempt - 1))

        raw = llm.chat(client, messages, model=model, temperature=temperature)
        raw_attempts.append(raw)

        code = clean_generated_code(raw)

        if not code:
            last_problems = ["empty result after extraction"]
        else:
            last_problems = validate_c_source(code, required_features)

        if not last_problems:
            if attempt > 1:
                log.info("Attempt %d/%d passed validation.", attempt, max_attempts)
            return code

        log.warning(
            "Attempt %d/%d failed validation: %s",
            attempt,
            max_attempts,
            "; ".join(last_problems),
        )

        if attempt < max_attempts:
            messages.append({"role": "assistant", "content": raw})
            messages.append({
                "role": "user",
                "content": FEEDBACK_TEMPLATE.format(
                    problems="\n".join(f"- {p}" for p in last_problems)
                ),
            })

    _dump_failed_attempts(raw_attempts)
    raise GenerationError(
        f"Generated C source failed sanity checks after {max_attempts} attempts, "
        "refusing to compile/flash it:\n"
        + "\n".join(f"  - {p}" for p in last_problems)
        + f"\nRaw model output for all attempts saved under "
        f"{FAILED_GENERATIONS_DIR.resolve()} for inspection. The model may need "
        "a different prompt, a lower-noise model, or a smaller/simpler request."
    )


def _dump_failed_attempts(raw_attempts: list[str]) -> None:
    try:
        FAILED_GENERATIONS_DIR.mkdir(parents=True, exist_ok=True)
        for i, attempt_raw in enumerate(raw_attempts, start=1):
            (FAILED_GENERATIONS_DIR / f"attempt_{i}.txt").write_text(
                attempt_raw, encoding="utf-8"
            )
    except OSError as e:
        log.warning("Could not dump failed attempts: %s", e)
