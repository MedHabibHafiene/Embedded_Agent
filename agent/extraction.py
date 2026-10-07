"""
Extraction & repair: raw LLM response text -> clean C source.

The model is instructed to wrap its output in sentinel markers; that is the
primary path. Everything else here is defensive recovery for models that
ramble, emit markdown fences, or use close-but-wrong HAL identifier names
that we can deterministically repair.
"""

import logging

from agent.prompts import SENTINEL_BEGIN, SENTINEL_END

log = logging.getLogger(__name__)

# Lines that look like chain-of-thought leakage rather than C code. Shared
# with validation.py, which rejects any source that still contains one.
REASONING_MARKERS = (
    "wait,", "wait ", "let me", "let's", "i need to", "i'll", "i will",
    "the user wants", "the prompt says", "main request:", "hardware context:",
    "here's a thinking process", "important:", "note:", "so i'll", "re-read",
)


def strip_reasoning_and_fences(text: str) -> str:
    """Remove stray markdown fences and interleaved reasoning lines wherever
    they appear in the text, not just at the start/end."""
    out_lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped in ("```", "```c", "```C"):
            continue
        if any(stripped.lower().startswith(m) for m in REASONING_MARKERS):
            continue
        out_lines.append(line)
    return "\n".join(out_lines)


def extract_last_complete_file(text: str) -> str:
    """If the model emitted the file more than once (e.g. a discarded draft
    followed by a final version), keep only the last complete attempt."""
    lines = text.splitlines()
    include_starts = [i for i, l in enumerate(lines) if l.startswith("#include")]
    if not include_starts:
        return text
    for start in reversed(include_starts):
        candidate = "\n".join(lines[start:])
        if candidate.count("int main(") >= 1:
            return candidate
    return "\n".join(lines[include_starts[-1]:])


def extract_code_from_response(raw: str) -> str:
    """Primary path: pull content out from between the sentinel markers -
    no escaping for the model to get wrong, so this should be the common
    case. Falls back to looser text-scraping only if sentinels are absent
    or malformed."""
    if SENTINEL_BEGIN in raw and SENTINEL_END in raw:
        start = raw.index(SENTINEL_BEGIN) + len(SENTINEL_BEGIN)
        end = raw.index(SENTINEL_END, start)
        return raw[start:end].strip()

    log.warning("Sentinel markers not found; falling back to text extraction.")
    cleaned = strip_reasoning_and_fences(raw)
    return extract_last_complete_file(cleaned)


def normalize_known_hal_aliases(code: str) -> str:
    """Deterministic repair of close-but-wrong HAL identifiers the model
    keeps producing. Cheaper and more reliable than another LLM round-trip."""
    replacements = {
        "RCC_SYSCLKSOURCE_PLLPLL": "RCC_SYSCLKSOURCE_PLLCLK",
        "RCC_HSICAL_DEFAULT": "RCC_HSICALIBRATION_DEFAULT",
        "DMA_InitStruct.DMABurst": "DMA_InitStruct.MemBurst",
        "hdma_mem_to_mem.Init.DMABurst": "hdma_mem_to_mem.Init.MemBurst",
        "DMA_BURST_SINGLE": "DMA_MBURST_SINGLE",
        "__HAL_PWR_REGULATOR_VOLTAGE_SCALE1()": "__HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE1)",
        "DMA2_Channel1": "DMA2_Stream1",
        "DMA_CHANNEL_1": "DMA_CHANNEL_7",
        "DMA_PDATA_WORD": "DMA_PDATAALIGN_WORD",
        "DMA_MDATA_WORD": "DMA_MDATAALIGN_WORD",
        "HAL_PWR_SetRegulatorVoltageScale(PWR_REGULATOR_VOLTAGE_SCALE1);": "__HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE1);",
    }
    for incorrect, correct in replacements.items():
        code = code.replace(incorrect, correct)
    code = code.replace("  __HAL_PWR_CLEAR_FLAG();\n", "")
    code = code.replace("  HAL_PWREx_EnablePWRCmd();\n", "")
    code = code.replace("__HAL_PWR_CLEAR_FLAG();\n", "")
    code = code.replace("HAL_PWREx_EnablePWRCmd();\n", "")
    code = code.replace(
        "RCC_OscInitStruct.PLL.PLLSource = RCC_SYSCLKSOURCE_PLLCLK;",
        "RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSI;",
    )
    return code


def clean_generated_code(raw: str) -> str:
    """Full output-cleaning pipeline: sentinel extraction (or fallback), then
    alias repair, then leftover fence trimming."""
    code = extract_code_from_response(raw).strip()
    code = normalize_known_hal_aliases(code)
    if code.startswith("```"):
        code = code.split("\n", 1)[1].strip()
    if code.endswith("```"):
        code = code[: -len("```")].strip()
    return code
