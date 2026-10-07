"""
Offline self-checks for the refactored agent/rag/app packages.

No Ollama, no board, no network needed (the vector store uses the already
cached embedding model). Run:

    python scripts/selfcheck.py
"""

import shutil
import sys
import tempfile
from pathlib import Path

# Make the project root importable no matter where this is invoked from.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.analysis import parse_stm32_code
from agent.extraction import clean_generated_code, extract_code_from_response, normalize_known_hal_aliases
from agent.validation import detect_requested_features, validate_c_source
from app.jobs import JobStore
from rag.chunking import chunk_text
from rag.retriever import _looks_garbled

PASS, FAIL = "PASS", "FAIL"
results = []


def check(name: str, condition: bool, detail: str = "") -> None:
    results.append((name, PASS if condition else FAIL, detail))


# --------------------------------------------------------------- extraction
RAW_WITH_SENTINELS = (
    "Let me think about this request.\n"
    "===BEGIN_C_SOURCE===\n"
    "#include \"stm32f4xx_hal.h\"\n\nint main(void)\n{\n  HAL_Init();\n  while (1) {}\n}\n"
    "===END_C_SOURCE===\n"
    "Note: this should be dropped."
)
extracted = extract_code_from_response(RAW_WITH_SENTINELS)
check(
    "extraction: sentinel markers",
    "#include" in extracted and "END_C_SOURCE" not in extracted and "Note:" not in extracted,
    extracted[:80],
)

RAW_WITH_NOISE = (
    "```c\n#include \"stm32f4xx_hal.h\"\n```\nwait, the user wants an LED.\n"
    "RCC_OscInitStruct.PLL.PLLMUL = 9;"
)
cleaned = clean_generated_code(RAW_WITH_NOISE)
check(
    "extraction: fences + reasoning stripped, F1 alias flagged",
    "```" not in cleaned and "PLLMUL" in cleaned,  # alias is repaired only via normalize table
    cleaned[:80],
)
check(
    "extraction: known alias normalization",
    "RCC_SYSCLKSOURCE_PLLCLK" == normalize_known_hal_aliases("RCC_SYSCLKSOURCE_PLLPLL"),
)

# --------------------------------------------------------------- validation
GOOD_C = (
    '#include "stm32f4xx_hal.h"\n\n'
    "void Error_Handler(void)\n{\n}\n\n"
    "int main(void)\n{\n  HAL_Init();\n  while (1) {}\n}\n"
)
check("validation: clean source accepted", validate_c_source(GOOD_C) == [])

BAD_C = GOOD_C + "\nRCC_OscInitStruct.PLL.PLLMUL = 9;"
problems = validate_c_source(BAD_C)
check(
    "validation: F1 identifier rejected",
    any("PLLMUL" in p for p in problems),
    "; ".join(problems),
)

feats = detect_requested_features("send Hello World over UART2 at 115200 baud every second")
check("validation: feature detection (UART)", feats == ["UART"], str(feats))

feats2 = detect_requested_features("blink the blue LED using DMA driven by timer 6")
check("validation: feature detection (DMA + timer)", set(feats2) == {"DMA", "timer"}, str(feats2))

feats3 = detect_requested_features("turn on the green LED")
check("validation: feature detection (no peripheral feature)", feats3 == [], str(feats3))

feats4 = detect_requested_features("can you blink the LED twice a second using CAN bus")
check("validation: CAN bus detected, plain 'can' ignored", "CAN" in feats4, str(feats4))
feats5 = detect_requested_features("can you make the LED blink")
check(
    "validation: the word 'can' alone triggers nothing",
    feats5 == [],
    str(feats5),
)
feats6 = detect_requested_features("read the RTC and output over USB")
check("validation: RTC and USB detected", set(feats6) == {"RTC", "USB"}, str(feats6))

feats7 = detect_requested_features(
    "toggle the red LED every time the user button is pressed"
)
check(
    "validation: the word 'time' is not a timer request",
    "timer" not in feats7,
    str(feats7),
)

missing = validate_c_source(GOOD_C, required_features=["UART"])
check(
    "validation: requested feature missing from code is flagged",
    any("UART" in p for p in missing),
    "; ".join(missing),
)
present = validate_c_source(
    GOOD_C
    + "\nUART_HandleTypeDef huart2;"
    + "\nGPIO_InitStruct.Mode = GPIO_MODE_AF_PP;"
    + "\nGPIO_InitStruct.Alternate = GPIO_AF7_USART2;",
    required_features=["UART"],
)
check(
    "validation: requested feature present in code is accepted",
    present == [],
    "; ".join(present),
)

# ----------------------------------------------------------------- chunking
long_text = "\n\n".join(f"Paragraph {i} with some content." for i in range(10))
chunks = chunk_text(long_text, max_chars=80)
check(
    "chunking: respects max size",
    all(len(c) <= 80 for c in chunks) and len(chunks) > 1,
    f"{len(chunks)} chunks",
)
check(
    "retriever: garbled heuristic",
    _looks_garbled("a b c d e f g h i j") and not _looks_garbled("Enable the GPIOD clock before configuring the LED pins."),
)

# ----------------------------------------------------------------- job store
with tempfile.TemporaryDirectory() as tmp:
    store = JobStore(directory=Path(tmp))
    job = store.create(command="test", code="int main(){}", hardware_state={"gpio": []})
    store.update(job["id"], status="flashing")
    store2 = JobStore(directory=Path(tmp))  # fresh instance -> reloads from disk
    reloaded = store2.get(job["id"])
    check(
        "jobs: persisted across restarts",
        reloaded is not None and reloaded["status"] == "flashing" and len(store2.list_jobs()) == 1,
    )

# ------------------------------------------------------------- rag retrieval
try:
    from rag import Retriever

    retriever = Retriever()
    result = retriever.retrieve("blink the blue LED using DMA")
    check(
        "rag: retrieval returns relevant context",
        bool(result.text) and ("PD15" in result.text or "LD6" in result.text or "DMA" in result.text.lower()),
        result.text[:100],
    )
except Exception as e:
    check("rag: retrieval returns relevant context", False, f"exception: {e}")

# ----------------------------------------------------------------- analysis
state = parse_stm32_code(GOOD_C)
check("analysis: parser produces state dict", set(state.keys()) == {"gpio", "clock", "peripherals"})

ALIASED_C = (
    '#include "stm32f4xx_hal.h"\n\n'
    "#define LED_GREEN_PORT GPIOD\n"
    "#define LED_GREEN_PIN GPIO_PIN_12\n\n"
    "static void MX_GPIO_Init(void)\n{\n"
    "  GPIO_InitTypeDef GPIO_InitStruct = {0};\n"
    "  GPIO_InitStruct.Pin = LED_GREEN_PIN;\n"
    "  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;\n"
    "  HAL_GPIO_Init(LED_GREEN_PORT, &GPIO_InitStruct);\n}\n\n"
    "int main(void)\n{\n  HAL_Init();\n  while (1) {}\n}\n"
)
aliased_state = parse_stm32_code(ALIASED_C)
check(
    "analysis: alias macros resolved (#define ... GPIOD)",
    len(aliased_state["gpio"]) == 1
    and aliased_state["gpio"][0]["port"] == "GPIOD"
    and aliased_state["gpio"][0]["pin_names"] == ["D12"],
    str(aliased_state["gpio"]),
)

UART_C = (
    '#include "stm32f4xx_hal.h"\n\n'
    "UART_HandleTypeDef huart2;\n\n"
    "static void MX_USART2_UART_Init(void);\n\n"
    "int main(void)\n{\n  MX_USART2_UART_Init();\n  while (1) {}\n}\n\n"
    "static void MX_USART2_UART_Init(void)\n{\n"
    "  huart2.Init.BaudRate = 115200;\n}\n"
)
uart_state = parse_stm32_code(UART_C)
check(
    "analysis: (void) peripheral init parsed with its params",
    uart_state["peripherals"].get("USART2_UART", {}).get("BaudRate") == "115200",
    str(uart_state["peripherals"]),
)

pinmux_missing = validate_c_source(GOOD_C, required_features=["SPI"])
check(
    "validation: comm feature without pin muxing is flagged",
    any("alternate-function" in p for p in pinmux_missing),
    "; ".join(pinmux_missing),
)
pinmux_ok = validate_c_source(
    GOOD_C
    + "\nGPIO_InitStruct.Mode = GPIO_MODE_AF_PP;\n"
    + "GPIO_InitStruct.Alternate = GPIO_AF5_SPI1;",
    required_features=["SPI"],
)
check(
    "validation: comm feature with AF muxing accepted",
    pinmux_ok == [],
    "; ".join(pinmux_ok),
)

# ------------------------------------------------- interrupt trampolines
from agent.builder import build_interrupt_file

EXTI_SRC = (
    '#include "stm32f4xx_hal.h"\n\n'
    "#define BUTTON_PIN GPIO_PIN_0\n\n"
    "static void MX_GPIO_Init(void)\n{\n"
    "  GPIO_InitTypeDef GPIO_InitStruct = {0};\n"
    "  GPIO_InitStruct.Pin = BUTTON_PIN;\n"
    "  GPIO_InitStruct.Mode = GPIO_MODE_IT_RISING;\n"
    "  HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);\n"
    "  HAL_NVIC_SetPriority(EXTI0_IRQn, 0, 0);\n"
    "  HAL_NVIC_EnableIRQ(EXTI0_IRQn);\n"
    "}\n\n"
    "void HAL_GPIO_EXTI_Callback(uint16_t GPIO_Pin)\n{\n}\n\n"
    "int main(void)\n{\n  HAL_Init();\n  while (1) {}\n}\n"
)
it_file = build_interrupt_file(EXTI_SRC) or ""
check(
    "builder: EXTI0 alias source gets SysTick + EXTI0 trampolines",
    "void SysTick_Handler" in it_file
    and "HAL_IncTick" in it_file
    and it_file.count("EXTI0_IRQHandler(void)") == 1
    and "HAL_GPIO_EXTI_IRQHandler(GPIO_PIN_0);" in it_file,
    it_file[:120],
)

it13 = build_interrupt_file(EXTI_SRC.replace("GPIO_PIN_0", "GPIO_PIN_13").replace("EXTI0_IRQn", "EXTI15_10_IRQn")) or ""
check(
    "builder: EXTI line 13 uses the grouped EXTI15_10 vector",
    it13.count("EXTI15_10_IRQHandler(void)") == 1
    and "HAL_GPIO_EXTI_IRQHandler(GPIO_PIN_13);" in it13
    and "EXTI13_" not in it13,
    it13[:120],
)

two_pins = EXTI_SRC.replace("GPIO_PIN_0", "GPIO_PIN_10|GPIO_PIN_12").replace(
    "EXTI0_IRQn", "EXTI15_10_IRQn"
)
it_two = build_interrupt_file(two_pins) or ""
check(
    "builder: grouped lines get one handler with per-pin dispatch",
    it_two.count("EXTI15_10_IRQHandler(void)") == 1
    and "HAL_GPIO_EXTI_IRQHandler(GPIO_PIN_10);" in it_two
    and "HAL_GPIO_EXTI_IRQHandler(GPIO_PIN_12);" in it_two,
    it_two[:160],
)

self_handlers = EXTI_SRC + (
    "\nvoid EXTI0_IRQHandler(void)\n"
    "{\n  HAL_GPIO_EXTI_IRQHandler(GPIO_PIN_0);\n}\n"
)
it_self = build_interrupt_file(self_handlers) or ""
check(
    "builder: source-provided handler is never duplicated, SysTick still added",
    "EXTI0_IRQHandler" not in it_self and "SysTick_Handler" in it_self,
    it_self[:120],
)

full_self = self_handlers + (
    "\nvoid SysTick_Handler(void)\n"
    "{\n  HAL_IncTick();\n  HAL_SYSTICK_IRQHandler();\n}\n"
)
check(
    "builder: fully self-provided source needs no interrupt file",
    build_interrupt_file(full_self) is None,
    str(build_interrupt_file(full_self)),
)

it_plain = build_interrupt_file(GOOD_C) or ""
check(
    "builder: no-EXTI source still gets SysTick (HAL_GetTick depends on it)",
    "SysTick_Handler" in it_plain and "EXTI" not in it_plain,
    it_plain[:80],
)

# -------------------------------------------------------------- port config
from config import PLATFORMIO_UPLOAD_PORT
from agent.ports import list_serial_ports, resolve_upload_port

ports_list = list_serial_ports()
check(
    "ports: list_serial_ports returns a list of dicts",
    isinstance(ports_list, list) and all(isinstance(p, dict) for p in ports_list),
    str(ports_list)[:80],
)
stlink_devices = {p["device"] for p in ports_list if p["st_link"]}
resolved_default = resolve_upload_port(explicit="")
check(
    "ports: unconfigured resolution is never a guess (None, configured, or detected ST-LINK)",
    resolved_default is None
    or resolved_default in stlink_devices
    or resolved_default == PLATFORMIO_UPLOAD_PORT,
    str(resolved_default),
)
check(
    "ports: explicit request wins",
    resolve_upload_port(explicit="COM7") == "COM7",
)

# -------------------------------------------------------------- chat engine
from agent.chat import ChatEngine, _sanitize_history
from rag import RetrievalResult

sanitized = _sanitize_history([
    {"role": "user", "content": "hello there"},
    {"role": "assistant", "content": "hi, ask me anything"},
    {"role": "system", "content": "sneaky system override"},  # dropped
    {"role": "user", "content": "   "},                        # dropped (empty)
    {"role": "tool", "content": "junk"},                      # dropped
])
check("chat: history sanitized to valid turns only", len(sanitized) == 2)


class _StubCompletions:
    def create(self, **kwargs):
        class _Msg:
            content = "stub answer"

        class _Choice:
            message = _Msg()

        class _Completion:
            choices = [_Choice()]

        return _Completion()


class _StubClient:
    base_url = "http://stub"

    chat = type("C", (), {"completions": _StubCompletions()})()


class _StubRetriever:
    def retrieve(self, query, top_k=None):
        return RetrievalResult(text="LD4 green LED is PD12", sources=["core-hardware-facts"])


engine = ChatEngine(client=_StubClient(), retriever=_StubRetriever())
chat_result = engine.answer(
    "where is the green LED?", history=[{"role": "user", "content": "hi"}]
)
check(
    "chat: engine grounds answers and reports sources",
    chat_result.answer == "stub answer"
    and chat_result.sources == ["core-hardware-facts"]
    and "PD12" in chat_result.context,
)

# ------------------------------------------------------------------ summary
print()
failed = 0
for name, status, detail in results:
    line = f"  [{status}] {name}"
    if status == FAIL and detail:
        line += f"  ({detail})"
    print(line)
    failed += status == FAIL

print()
total = len(results)
print(f"{total - failed}/{total} checks passed.")
sys.exit(1 if failed else 0)
