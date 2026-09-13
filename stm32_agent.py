#!/usr/bin/env python3
"""
STM32 Firmware Generation Agent - Pragmatic version (fixed).

This script performs the full autonomous workflow:
  1. Retrieve STM32 hardware context from ChromaDB RAG store.
  2. Query a local Ollama model to generate HAL C code.
  3. Write the generated code into a PlatformIO project.
  4. Compile with `pio run`.
  5. Flash with `pio run --target upload`.

Note: The LLM may include reasoning text or markdown fences; the script's
extraction logic does its best to isolate the C source.

Fixes applied vs. original:
  - config.py's PLATFORMIO_UPLOAD_PORT fallback was imported but dead code;
    now it's actually used.
  - Port resolution order was broken (hardcoded "COM5" default made
    auto-detect unreachable). Now: env var > config.py > auto-detect > COM5.
  - COM5 fallback was Windows-only; auto-detect now also covers Windows via
    pyserial so non-Windows machines don't silently get a bogus port.
  - subprocess calls had no timeout and could hang indefinitely on a wedged
    ST-LINK; both compile and upload now have timeouts.
  - Error messages only surfaced stderr; PlatformIO frequently reports the
    real compiler/linker error on stdout, so both are now included.
  - Flashing is autonomous by default in the original; added an explicit
    confirmation gate (skip with --yes) since this writes code straight to
    hardware without human review of the generated firmware.
  - init_ollama_client() used to construct a manual httpx.Client(proxies={})
    to work around an old openai/httpx incompatibility. Current httpx
    versions removed the `proxies` kwarg entirely, so that workaround now
    crashes with "Client.__init__() got an unexpected keyword argument
    'proxies'". Removed it - openai.OpenAI() builds its own httpx client
    correctly on current library versions.
"""

import os
import sys
import re
import json
import textwrap
import shutil
from datetime import datetime
from pathlib import Path

import openai
import chromadb
import subprocess
from config import OLLAMA_API_KEY, OLLAMA_BASE_URL, OLLAMA_MODEL, PLATFORMIO_UPLOAD_PORT
from stm32_parser import parse_stm32_code

BUILD_TIMEOUT_SEC = 120
UPLOAD_TIMEOUT_SEC = 60


# --------------------------------------------------------------
# 1. CLIENT CONFIG – Ollama OpenAI-compatible endpoint
# --------------------------------------------------------------
def init_ollama_client() -> openai.OpenAI:
    api_key = os.getenv("OLLAMA_API_KEY") or OLLAMA_API_KEY
    if not api_key:
        raise RuntimeError(
            "OLLAMA_API_KEY environment variable not set. "
            "Export it e.g.: export OLLAMA_API_KEY=your_key_here"
        )

    return openai.OpenAI(
        base_url=os.getenv("BASE_URL") or OLLAMA_BASE_URL,
        api_key=api_key,
    )


# --------------------------------------------------------------
# 2. RAG VECTOR STORE – ChromaDB seeded with STM32 docs
# --------------------------------------------------------------
SEED_DOCS = [
    "STM32F4-Discovery board (board id disco_f407vg, MCU STM32F407VGT6) user LEDs: "
    "LD4 green=PD12, LD3 orange=PD13, LD5 red=PD14, LD6 blue=PD15. All four are "
    "active-high and on GPIO port D; enable clock via RCC_AHB1ENR GPIODEN before use.",
    "Do not use PC13 as an LED pin on the disco_f407vg board - PC13 is the blue "
    "LED pin on other, unrelated STM32F407 boards (e.g. generic minimal/'Black "
    "Pill' dev boards), not on the STM32F4-Discovery board used in this project.",
    "PA5 on the disco_f407vg board is SPI1_SCK and is not wired to any LED. Do "
    "not drive PA5 expecting a visible LED response.",
    "Clock: HSE 8MHz -> PLL (M=8 N=336 P=2 Q=7) -> 168MHz system clock. HSI "
    "16MHz with M=8 N=168 P=2 also reaches 168MHz and is acceptable if HSE "
    "is not required.",
    "On STM32F4 (HAL_RCC, RCC_OscInitTypeDef), the PLL sub-struct field names "
    "are exactly: PLLState, PLLSource, PLLM, PLLN, PLLP, PLLQ (e.g. "
    "RCC_OscInitStruct.PLL.PLLM = 8; .PLLN = 336; .PLLP = RCC_PLLP_DIV2; "
    ".PLLQ = 7;). Valid PLLP macros are RCC_PLLP_DIV2/DIV4/DIV6/DIV8.",
    "Do not use PLLMUL, PLLDIV, PLLDPLL, RCC_PLL_MULx, RCC_PLL_DIVx, or "
    "RCC_PLL_DPLL_NONE anywhere in STM32F4 code - those are STM32F1-family "
    "RCC_PLLConfig field/macro names and do not exist in the STM32F4 HAL. "
    "Using them will fail to compile.",
    "For STM32F4 HAL clock setup, use RCC_HSICALIBRATION_DEFAULT (not "
    "RCC_HSICAL_DEFAULT). RCC_ClkInitTypeDef has SYSCLKSource, "
    "AHBCLKDivider, APB1CLKDivider, and APB2CLKDivider; it has no "
    "SystemClockDivider field. The PLL clock source macro is "
    "RCC_SYSCLKSOURCE_PLLCLK.",
    "GPIO init: enable the port clock first (e.g. RCC_AHB1ENR), then set "
    "MODE_OUTPUT_PP, PULL_NONE, SPEED_50MHz for a simple LED output.",
    "UART2: TX=PA2 RX=PA3, enable clock via RCC_APB1ENR USART2EN.",
    "SPI1 default pins: SCK=PA5 MISO=PA6 MOSI=PA7 - these are unrelated to any "
    "LED and should only be used for SPI peripherals.",
]


def _seed_fingerprint(docs: list[str]) -> str:
    import hashlib
    return hashlib.sha256("\n".join(docs).encode("utf-8")).hexdigest()


def _looks_garbled(text: str, threshold: float = 0.4) -> bool:
    """Heuristic: real prose doesn't have a huge fraction of 1-character
    'words'. Catches mis-decoded / mangled text before it reaches the LLM."""
    tokens = [t for t in text.split(" ") if t]
    if len(tokens) < 6:
        return False
    single_char_ratio = sum(1 for t in tokens if len(t) == 1) / len(tokens)
    return single_char_ratio > threshold


class Stm32RagStore:
    def __init__(self, persist_dir: str = "./chroma_db"):
        self.persist_dir = Path(persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=str(self.persist_dir))
        self.collection = self.client.get_or_create_collection("stm32_docs")
        self._seed_if_needed()

    def _fingerprint_path(self) -> Path:
        return self.persist_dir / "seed_fingerprint.txt"

    def _seed_if_needed(self):
        expected_fp = _seed_fingerprint(SEED_DOCS)
        fp_path = self._fingerprint_path()
        stored_fp = fp_path.read_text().strip() if fp_path.exists() else None

        # Reseed whenever the collection is empty OR its content doesn't
        # match what this version of the code expects to have seeded
        # (stale/corrupted persisted data, or seed docs changed in code).
        if self.collection.count() > 0 and stored_fp == expected_fp:
            return

        if self.collection.count() > 0:
            print("[RAG][WARN] Persisted collection doesn't match expected "
                  "seed content (stale or corrupted store) - reseeding.")
            existing_ids = self.collection.get()["ids"]
            if existing_ids:
                self.collection.delete(ids=existing_ids)

        ids = [f"stm32-doc-{i}" for i in range(len(SEED_DOCS))]
        self.collection.add(documents=SEED_DOCS, ids=ids)
        fp_path.write_text(expected_fp, encoding="utf-8")
        print(f"[RAG] Seeded {len(SEED_DOCS)} STM32 reference documents.")

    def retrieve(self, query: str, top_k: int = 5) -> str:
        results = self.collection.query(query_texts=[query], n_results=top_k)
        chunks = results["documents"][0]

        clean_chunks = []
        for chunk in chunks:
            if _looks_garbled(chunk):
                print(f"[RAG][WARN] Discarding garbled retrieved chunk: {chunk[:60]!r}...")
                continue
            clean_chunks.append(chunk)

        if not clean_chunks:
            print("[RAG][WARN] All retrieved context looked garbled/corrupted; "
                  "proceeding with no hardware context. Consider clearing "
                  "the chroma_db directory.")
            return ""

        return "\n".join(clean_chunks)


# --------------------------------------------------------------
# 3. CODE GENERATION ENGINE – LLM via local Ollama server
# --------------------------------------------------------------
SENTINEL_BEGIN = "===BEGIN_C_SOURCE==="
SENTINEL_END = "===END_C_SOURCE==="
MAX_GENERATION_ATTEMPTS = 3

SYSTEM_PROMPT = """You are an embedded software engineer working with STM32 microcontrollers.

Respond with nothing but the two sentinel lines below and the raw C source
code between them. No markdown fences, no reasoning, no commentary before or
after, no restating these instructions, no drafts - output the file exactly
once.

===BEGIN_C_SOURCE===
<the complete C file goes here, verbatim, no escaping needed>
===END_C_SOURCE===

The C code must:
- Include "stm32f4xx_hal.h"
- Have exactly one main() calling HAL_Init(), a clock-config function, GPIO
  init for an LED, and a while(1) loop that keeps the LED on.
- Declare Error_Handler after the includes and define it exactly once at the
  bottom of the file as: void Error_Handler(void) { while(1){} }
- Use exact HAL type names (GPIO_InitTypeDef, RCC_OscInitTypeDef, etc.).
- If the user requests DMA, the firmware must actually configure and use DMA:
    declare a DMA_HandleTypeDef, enable the required DMA controller clock, set
    DMA_InitTypeDef fields, call HAL_DMA_Init, and perform a HAL_DMA_Start or
    HAL_DMA_Start_IT transfer from a memory buffer to the intended peripheral or
    memory destination. For DMA_MEMORY_TO_PERIPH transfers to a fixed register,
    use PeriphInc = DMA_PINC_DISABLE and MemInc = DMA_MINC_ENABLE. Check the
    return value of HAL_DMA_Start and call HAL_DMA_PollForTransfer before using
    the destination. Do not claim DMA while only calling HAL_GPIO_WritePin.
- For STM32F4 clock setup, use RCC_HSICALIBRATION_DEFAULT, never
    RCC_HSICAL_DEFAULT. Do not assign SystemClockDivider because that field does
    not exist in RCC_ClkInitTypeDef; use SYSCLKSource, AHBCLKDivider,
    APB1CLKDivider, and APB2CLKDivider. Set ClockType to
    RCC_CLOCKTYPE_HCLK | RCC_CLOCKTYPE_SYSCLK | RCC_CLOCKTYPE_PCLK1 |
    RCC_CLOCKTYPE_PCLK2. Use RCC_SYSCLKSOURCE_PLLCLK exactly for the PLL clock
    source.
- Keep comments minimal.
- Contain no backticks, no markdown, and no meta-commentary of any kind.

Lay the file out exactly like STM32CubeMX-generated code, in this order, with
these section-banner comments present even when a section is short:

/* Includes ------------------------------------------------------------*/
#include "stm32f4xx_hal.h"

/* Private define --------------------------------------------------------*/
    (#define constants only - pin aliases, thresholds, timing constants)

/* Private variables -------------------------------------------------------*/
    (every global/static variable and peripheral handle - volatile flags,
    HandleTypeDef instances, etc. - declared once, here, and nowhere else)

/* Private function prototypes -----------------------------------------*/
    (forward declaration for every function defined below main: Error_Handler,
    SystemClock_Config, and one MX_<PERIPHERAL>_Init per peripheral used. Each
    prototype's linkage must match its definition exactly, word for word,
    including the "static" keyword - e.g. if SystemClock_Config is defined as
    "static void SystemClock_Config(void)", its prototype here must also read
    "static void SystemClock_Config(void);", never plain "void
    SystemClock_Config(void);". SystemClock_Config and every MX_*_Init
    function are static; Error_Handler is not static)

int main(void)
{
  /* MCU Configuration--------------------------------------------------*/
  HAL_Init();

  /* Configure the system clock */
  SystemClock_Config();

  /* Initialize all configured peripherals */
  MX_GPIO_Init();
  (one MX_<PERIPHERAL>_Init() call per peripheral actually used - e.g.
  MX_GPIO_Init, MX_TIM2_Init, MX_DMA_Init - main() only calls these, it never
  configures a GPIO_InitTypeDef, enables a peripheral clock, or fills any
  other peripheral struct directly)

  /* Infinite loop */
  while (1)
  {
    ...
  }
}

/* System Clock Configuration --------------------------------------------*/
static void SystemClock_Config(void)
{
  ...
}

/* <PERIPHERAL> initialization function ------------------------------*/
static void MX_GPIO_Init(void)
{
  ...
}
(repeat one such function per peripheral used, in the same order main() calls
them; interrupt handlers and HAL callbacks such as HAL_GPIO_EXTI_Callback go
after the last MX_*_Init function and before Error_Handler)

/* Error handler -----------------------------------------------------*/
void Error_Handler(void)
{
  while (1) {}
}

If the user request describes a button/pin being held for a duration before
something happens (e.g. "blink when the button is held for 1 second", "do X
if pressed for N ms") - as opposed to a plain press/release toggle - follow
this exact pattern; do not decide the outcome inside the EXTI callback:
- The EXTI callback (HAL_GPIO_EXTI_Callback) ONLY records state: on the
  rising edge (pin reads SET) it sets a volatile "is currently pressed" flag
  and stores HAL_GetTick() as the press-start timestamp; on the falling edge
  it clears the "is currently pressed" flag. It never itself computes a
  duration, never itself decides whether the hold was long enough, and never
  toggles or writes to the target GPIO.
- The while(1) loop in main() is where the duration is evaluated and the
  effect happens, every iteration: read the "is currently pressed" flag and
  compare HAL_GetTick() minus the stored press-start timestamp against the
  required threshold; only while both the flag is set AND the elapsed time
  has passed the threshold does the requested effect run (e.g. toggling an
  LED every N ms using a second HAL_GetTick()-based timer, exactly like the
  non-blocking blink pattern already used elsewhere in main()). When the flag
  is clear, undo the effect immediately (e.g. write the LED pins back to
  GPIO_PIN_RESET) - do not wait for a release-and-re-press cycle to react.
- Never make the requested effect depend on the button being released first;
  a request phrased as "while held" or "when held for N seconds" must take
  effect during the hold, the moment the threshold is crossed, not after the
  button goes back up.

Additional style rules:
- Every peripheral-init function is named MX_<PERIPHERAL>_Init (e.g.
  MX_GPIO_Init, MX_TIM2_Init), its forward declaration matches its definition
  exactly - including the "static" keyword, present in both or absent from
  both, never one and not the other - and it is only ever called from main().
- Leave one blank line between logical groups of statements inside a function
  (e.g. between enabling a clock and filling a GPIO_InitTypeDef), but no blank
  line inside a single struct-field-assignment block.
- Use consistent 2-space indentation; every function opens its brace on its
  own line; if/for/while open their brace on the same line.
- No unused variables, no unused includes, no dead code, no peripheral
  initialized more than once."""


CODE_PROMPT_TEMPLATE = """User request: {command}

Relevant hardware context (from ChromaDB):
{context}

Generate the firmware C file following the system prompt above. Respond with
ONLY the sentinel-wrapped C source described in the system prompt."""


FEEDBACK_TEMPLATE = """Your previous attempt was rejected for these reasons:
{problems}

Fix all of the issues above. Output exactly one complete, correct C file,
wrapped in the sentinel lines, with nothing else in your response."""


REASONING_MARKERS = (
    "wait,", "wait ", "let me", "let's", "i need to", "i'll", "i will",
    "the user wants", "the prompt says", "main request:", "hardware context:",
    "here's a thinking process", "important:", "note:", "so i'll", "re-read",
)


def _strip_reasoning_and_fences(text: str) -> str:
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


def _extract_last_complete_file(text: str) -> str:
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


def _extract_code_from_response(raw: str) -> str:
    """Primary path: pull content out from between the sentinel markers -
    no escaping for the model to get wrong, so this should be the common
    case. Falls back to looser text-scraping only if sentinels are absent
    or malformed."""
    if SENTINEL_BEGIN in raw and SENTINEL_END in raw:
        start = raw.index(SENTINEL_BEGIN) + len(SENTINEL_BEGIN)
        end = raw.index(SENTINEL_END, start)
        return raw[start:end].strip()

    print("[LLM][WARN] Sentinel markers not found; falling back to text extraction.")
    cleaned = _strip_reasoning_and_fences(raw)
    return _extract_last_complete_file(cleaned)


def _normalize_known_hal_aliases(code: str) -> str:
    replacements = {
        "RCC_SYSCLKSOURCE_PLLPLL": "RCC_SYSCLKSOURCE_PLLCLK",
        "RCC_HSICAL_DEFAULT": "RCC_HSICALIBRATION_DEFAULT",
        "DMA_InitStruct.DMABurst": "DMA_InitStruct.MemBurst",
        "hdma_mem_to_mem.Init.DMABurst": "hdma_mem_to_mem.Init.MemBurst",
        "DMA_BURST_SINGLE": "DMA_MBURST_SINGLE",
    }
    for incorrect, correct in replacements.items():
        code = code.replace(incorrect, correct)
    code = code.replace(
        "RCC_OscInitStruct.PLL.PLLSource = RCC_SYSCLKSOURCE_PLLCLK;",
        "RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSI;",
    )
    return code


FORBIDDEN_F1_IDENTIFIERS = {
    "PLLMUL": "PLLM and PLLN (this is an STM32F1-only field; STM32F4 has no single PLLMUL)",
    "PLLDIV": "PLLP (this is an STM32F1-only field name)",
    "PLLDPLL": "nothing - this field does not exist in any STM32 HAL and should be removed",
    "RCC_PLL_MUL": "setting RCC_OscInitStruct.PLL.PLLN directly (STM32F1-style multiplier macro)",
    "RCC_PLL_DIV": "RCC_PLLP_DIVx (STM32F1-style divider macro; F4 uses RCC_PLLP_DIVx)",
    "RCC_PLL_DPLL_NONE": "omitting this field entirely; it is not part of any real STM32 HAL",
}

FORBIDDEN_STM32F4_IDENTIFIERS = {
    "RCC_SYSCLKSOURCE_PLLPLL": "RCC_SYSCLKSOURCE_PLLCLK",
    "RCC_HSICAL_DEFAULT": "RCC_HSICALIBRATION_DEFAULT",
}


def validate_c_source(code: str, require_dma: bool = False) -> list[str]:
    """Return a list of problems found; empty list means it looks sane
    enough to hand to the compiler."""
    problems = []
    if "```" in code:
        problems.append("stray markdown fence ``` still present")
    if code.count("int main(") != 1:
        problems.append(f"expected exactly one 'int main(', found {code.count('int main(')}")
    error_handler_defs = len(re.findall(r"void\s+Error_Handler\s*\(\s*void\s*\)\s*\{", code))
    if error_handler_defs != 1:
        problems.append(f"expected exactly one Error_Handler definition, found {error_handler_defs}")
    if code.count("{") != code.count("}"):
        problems.append(f"unbalanced braces ({code.count('{')} '{{' vs {code.count('}')} '}}')")
    lower = code.lower()
    if any(marker.lower() in lower for marker in REASONING_MARKERS):
        problems.append("leftover reasoning/meta text detected in source")
    for bad_token, correction in FORBIDDEN_F1_IDENTIFIERS.items():
        if bad_token in code:
            problems.append(
                f"uses '{bad_token}', which does not exist in the STM32F4 HAL "
                f"(it's an STM32F1-family name) - use {correction} instead"
            )
    for bad_token, correction in FORBIDDEN_STM32F4_IDENTIFIERS.items():
        if bad_token in code:
            problems.append(
                f"uses invalid STM32F4 HAL identifier '{bad_token}'; "
                f"replace it with '{correction}'"
            )
    if require_dma:
        dma_requirements = {
            "DMA_HandleTypeDef": "a DMA handle declaration",
            "HAL_DMA_Init": "DMA initialization with HAL_DMA_Init",
            "HAL_DMA_Start": "a DMA transfer with HAL_DMA_Start or HAL_DMA_Start_IT",
        }
        for token, description in dma_requirements.items():
            if token not in code:
                problems.append(f"DMA request requires {description}")
        if "__HAL_RCC_DMA" not in code:
            problems.append("DMA request requires enabling a DMA controller clock")
    return problems


def _list_ollama_models(base_url: str) -> list[str]:
    """Query Ollama's native /api/tags endpoint (not the OpenAI-compat path)
    to list locally pulled models, for a more actionable error message."""
    import urllib.request

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


def generate_firmware(
    client: openai.OpenAI,
    command: str,
    context: str,
    model: str = OLLAMA_MODEL,
    max_attempts: int = MAX_GENERATION_ATTEMPTS,
) -> str:
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

        try:
            completion = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                top_p=1,
                max_tokens=2500,
                stream=False,
            )
        except openai.NotFoundError as e:
            # Model-not-pulled is a setup problem, not a bad generation -
            # retrying with the same missing model would just fail again.
            base_url = str(client.base_url)
            available = _list_ollama_models(base_url)
            hint = (
                f"Models available on this Ollama server: {', '.join(available)}"
                if available
                else "Could not reach the Ollama server to list models - "
                     "is it running (`ollama serve`)?"
            )
            raise RuntimeError(
                f"Model '{model}' is not available at {base_url}.\n"
                f"{hint}\n"
                f"Pull it with: ollama pull {model}\n"
                f"Or point at an installed model with: set OLLAMA_MODEL=<name> "
                f"(PowerShell: $env:OLLAMA_MODEL=\"<name>\")"
            ) from e

        raw = (completion.choices[0].message.content or "").strip()
        raw_attempts.append(raw)

        code = _normalize_known_hal_aliases(_extract_code_from_response(raw).strip())
        if code.startswith("```"):
            code = code.split("\n", 1)[1].strip()
        if code.endswith("```"):
            code = code[: -len("```")].strip()

        if not code:
            last_problems = ["empty result after extraction"]
        else:
            last_problems = validate_c_source(
                code,
                require_dma=bool(re.search(r"\bDMA\b", command, re.IGNORECASE)),
            )

        if not last_problems:
            if attempt > 1:
                print(f"[LLM] Attempt {attempt}/{max_attempts} passed validation.")
            return code

        print(f"[LLM][WARN] Attempt {attempt}/{max_attempts} failed validation: "
              f"{'; '.join(last_problems)}")

        if attempt < max_attempts:
            # Feed the failure back to the model so the retry is corrective,
            # not just another independent roll of the dice.
            messages.append({"role": "assistant", "content": raw})
            messages.append({
                "role": "user",
                "content": FEEDBACK_TEMPLATE.format(
                    problems="\n".join(f"- {p}" for p in last_problems)
                ),
            })

    debug_dir = Path("failed_generations")
    debug_dir.mkdir(exist_ok=True)
    for i, attempt_raw in enumerate(raw_attempts, start=1):
        (debug_dir / f"attempt_{i}.txt").write_text(attempt_raw, encoding="utf-8")

    raise RuntimeError(
        f"Generated C source failed sanity checks after {max_attempts} attempts, "
        "refusing to compile/flash it:\n"
        + "\n".join(f"  - {p}" for p in last_problems)
        + f"\nRaw model output for all attempts saved under {debug_dir.resolve()} "
        + "for inspection. The model may need a different prompt, a lower-noise "
        + "model, or a smaller/simpler request."
    )


# --------------------------------------------------------------
# 4. BUILD & FLASH AUTOMATION – PlatformIO subprocess
# --------------------------------------------------------------
def detect_stlink_port() -> str | None:
    """Return the COM/tty port of an ST-LINK adaptor, or None. Works cross-platform."""
    import serial.tools.list_ports
    ports = list(serial.tools.list_ports.comports())
    for p in ports:
        hwid = getattr(p, "hwid", "") or ""
        desc = getattr(p, "description", "") or ""
        if re.search(r"STMicroelectronics|ST-Link|0483:001[0-9]", hwid + " " + desc, re.I):
            return getattr(p, "device", None)
    return None


def write_code_to_platformio(project_dir: Path, source_code: str, filename: str = "main.c") -> None:
    src_dir = project_dir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    out_file = src_dir / filename
    out_file.write_text(source_code, encoding="utf-8")
    print(f"[Build] Written firmware to {out_file}")

    interrupt_file = src_dir / "stm32f4xx_it.c"
    interrupt_file.write_text(
        '#include "stm32f4xx_hal.h"\n\n'
        "void SysTick_Handler(void)\n"
        "{\n"
        "    HAL_IncTick();\n"
        "    HAL_SYSTICK_IRQHandler();\n"
        "}\n",
        encoding="utf-8",
    )
    print(f"[Build] Written SysTick handler to {interrupt_file}")


def resolve_upload_port() -> str:
    """Resolution order: env var > config.py default > auto-detect > 'COM5' last resort."""
    port = os.getenv("PLATFORMIO_UPLOAD_PORT")
    if port:
        return port

    if PLATFORMIO_UPLOAD_PORT:
        print(f"[Build] Using upload port from config.py: {PLATFORMIO_UPLOAD_PORT}")
        return PLATFORMIO_UPLOAD_PORT

    detected = detect_stlink_port()
    if detected:
        print(f"[Build] Auto-detected ST-LINK on port: {detected}")
        return detected

    fallback = "COM5" if sys.platform.startswith("win") else "/dev/ttyACM0"
    print(
        f"[WARN] No ST-LINK auto-detected and no port configured; "
        f"falling back to {fallback}. Set PLATFORMIO_UPLOAD_PORT to override."
    )
    return fallback


def ensure_platformio_ini(project_dir: Path) -> None:
    """Write a minimal platformio.ini for the STM32F407 Discovery board."""
    ini_path = project_dir / "platformio.ini"
    port = resolve_upload_port()

    ini_content = textwrap.dedent(
        f"""\
        [platformio]
        default_envs = disco_f407vg

        [env:disco_f407vg]
        platform = ststm32
        board = disco_f407vg
        framework = stm32cube
        upload_port = {port}
        monitor_port = {port}
        debug_port = {port}

        [env]
        default_envs = disco_f407vg

        ; Platform packages
        platform_packages =
            platformio/framework-stm32cubef4@^1.28.3
        """
    ).lstrip("\n")

    if not ini_path.exists() or ini_path.read_text().strip() != ini_content.strip():
        ini_path.write_text(ini_content, encoding="utf-8")
        print(f"[Build] Wrote platformio.ini with upload_port={port}")


def _run(cmd: list[str], cwd: Path, timeout: int, step: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(
            f"[Build] {step} timed out after {timeout}s. "
            f"Check that the board/ST-LINK is connected and not stuck in a bad state."
        ) from e


def archive_and_remove_source(project_dir: Path, command: str, status: str = "flashed") -> None:
    source_file = project_dir / "src" / "main.c"
    if not source_file.exists():
        return

    history_dir = project_dir / "history" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    history_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_file, history_dir / "main.c")
    (history_dir / "metadata.json").write_text(
        json.dumps(
            {
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "command": command,
                "status": status,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[Build] Archived firmware to {history_dir}")
    source_file.unlink()
    print(f"[Build] Cleaned up {source_file} after flashing.")


def build_and_flash(
    project_dir: Path,
    auto_confirm: bool = False,
    command: str = "",
) -> None:
    ensure_platformio_ini(project_dir)

    print("[Build] Running 'pio run' (compilation)...")
    compile_result = _run(["pio", "run"], project_dir, BUILD_TIMEOUT_SEC, "Compilation")
    if compile_result.returncode != 0:
        raise RuntimeError(
            f"[Build] Compilation failed (exit {compile_result.returncode}):\n"
            f"--- stdout ---\n{compile_result.stdout}\n"
            f"--- stderr ---\n{compile_result.stderr}"
        )
    print("[Build] Compilation succeeded.")

    if not auto_confirm:
        reply = input(
            "[Build] Firmware compiled. Flash it to the connected board now? [y/N] "
        ).strip().lower()
        if reply != "y":
            print("[Build] Flash skipped by user.")
            return

    print("[Build] Running 'pio run --target upload' (flashing)...")
    upload_result = _run(
        ["pio", "run", "--target", "upload"], project_dir, UPLOAD_TIMEOUT_SEC, "Upload"
    )
    if upload_result.returncode != 0:
        raise RuntimeError(
            f"[Build] Upload failed (exit {upload_result.returncode}):\n"
            f"--- stdout ---\n{upload_result.stdout}\n"
            f"--- stderr ---\n{upload_result.stderr}"
        )
    print("[Build] Firmware successfully flashed to the board.")

    archive_and_remove_source(project_dir, command)


# --------------------------------------------------------------
# 5. ORCHESTRATION PIPELINE – end-to-end entry point
# --------------------------------------------------------------
def orchestration_pipeline(
    command: str,
    project_dir: str = "./stm32_project",
    rag_store: "Stm32RagStore | None" = None,
    client: openai.OpenAI | None = None,
    auto_confirm: bool = False,
) -> None:
    if client is None:
        client = init_ollama_client()
    if rag_store is None:
        rag_store = Stm32RagStore()

    print(f"[RAG] Querying documentation for: '{command}'")
    context = rag_store.retrieve(command)
    print("[RAG] Retrieved context (shown for debugging):")
    print(context)

    print("[LLM] Generating firmware C code...")
    generated = generate_firmware(client, command, context)
    if isinstance(generated, str):
        source_code = generated
        result = {
            "status": "success",
            "c_code": source_code,
            "hardware_state": parse_stm32_code(source_code),
        }
    else:
        result = generated
        source_code = result["c_code"]
    print(f"[LLM] Code generation finished. Extracted {len(source_code)} characters.")

    proj_path = Path(project_dir)
    proj_path.mkdir(parents=True, exist_ok=True)
    write_code_to_platformio(proj_path, source_code)

    print("[BUILD] Starting build & flash pipeline...")
    build_and_flash(proj_path, auto_confirm=auto_confirm, command=command)
    return result


# --------------------------------------------------------------
# CLI entry point
# --------------------------------------------------------------
if __name__ == "__main__":
    args = sys.argv[1:]
    auto_confirm_flag = "--yes" in args or "-y" in args
    args = [a for a in args if a not in ("--yes", "-y")]

    if len(args) < 1:
        sys.stderr.write(
            "Usage: python stm32_agent.py \"<natural-language command>\" [--yes]\n"
            'Example: python stm32_agent.py "Turn on the red diode continuously"\n'
        )
        sys.exit(1)

    user_command = " ".join(args)
    try:
        orchestration_pipeline(user_command, auto_confirm=auto_confirm_flag)
    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
        sys.exit(1)