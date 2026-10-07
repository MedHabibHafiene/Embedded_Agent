"""
Validation: pure, side-effect-free checks on generated C source.

Two responsibilities:
  - detect which hardware features the user's command asks for (DMA, UART,
    SPI, ...) and verify the firmware actually configures/uses them - a
    generic "did you do what was asked" check, with no opinion about HOW
  - reject sources that are structurally broken or that use identifiers
    from the wrong STM32 family

This module deliberately contains no task-specific knowledge (no mandated
timers, pins, or register patterns) - implementation choice belongs to the
model informed by the retrieved documentation.
"""

import re

from agent.extraction import REASONING_MARKERS

# Feature name -> regex(es) that flag a command as requesting that feature.
# Trailing word characters are allowed so peripheral instances match too
# (UART2, DMA2, SPI1, I2C1, TIM6, ...).
FEATURE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "DMA": (r"\bDMA\w*",),
    "UART": (r"\b(?:UART|USART)\w*|\bserial\b",),
    "SPI": (r"\bSPI\w*",),
    "I2C": (r"\bI2C\w*|\bTWI\b",),
    # "timer"/"timers" in prose, or a TIM<TIMn> peripheral name like TIM4 -
    # deliberately NOT \bTIM\w*, which also matches "time"/"times" (the
    # feature regexes run case-insensitively).
    "timer": (r"\btimers?\b|\btim\d+\w*",),
    "PWM": (r"\bPWM\w*",),
    "ADC": (r"\bADC\w*|\banalog(?:\s+(?:input|read|value|signal))?\b",),
    "interrupt": (r"\binterrupt",),
    "watchdog": (r"\bwatchdog\b|\bIWDG\w*|\bWWDG\w*",),
    # CAN needs a discriminator ("CAN bus", "CAN1/2") so the English word
    # "can" never triggers it (matching is case-insensitive).
    "CAN": (r"\bCAN(?:\s+bus|[12])\b",),
    "DAC": (r"\bDAC\w*",),
    "RTC": (r"\bRTC\b",),
    "USB": (r"\bUSB\w*",),
}

# Feature name -> tokens of which at least one must appear in the code for
# the firmware to count as actually using that feature.
FEATURE_TOKENS: dict[str, tuple[str, ...]] = {
    "DMA": (
        "DMA_HandleTypeDef", "HAL_DMA_Init", "HAL_DMA_Start", "DMA2D",
        "HAL_TIM_Base_Start_DMA", "__HAL_LINKDMA", "__HAL_RCC_DMA",
    ),
    "UART": ("USART", "UART"),
    "SPI": ("SPI",),
    "I2C": ("I2C",),
    "timer": ("TIM",),
    "PWM": ("HAL_TIM_PWM",),
    "ADC": ("ADC",),
    "interrupt": ("NVIC", "IRQHandler"),
    "watchdog": ("IWDG", "WWDG"),
    "CAN": ("CAN",),
    "DAC": ("DAC",),
    "RTC": ("RTC",),
    "USB": ("USB", "PCD", "HAL_PCD"),
}


# Communication peripherals physically require alternate-function pin muxing
# on STM32F4 - without it the peripheral never reaches its pins.
PIN_MUX_REQUIRED_FEATURES = {"UART", "SPI", "I2C"}
PIN_MUX_TOKENS = ("GPIO_AF", "GPIO_MODE_AF", "MspInit")


def detect_requested_features(command: str) -> list[str]:
    """Return the feature names the command explicitly asks for."""
    requested = []
    for feature, patterns in FEATURE_KEYWORDS.items():
        if any(re.search(pattern, command, re.IGNORECASE) for pattern in patterns):
            requested.append(feature)
    return requested


# Identifiers that are objectively wrong for the STM32F4 HAL (they belong to
# other STM32 families) - checked regardless of the request.
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
    "DMA_FIFO_MODE_DISABLE": "DMA_FIFOMODE_DISABLE",
    "DMA_FIFO_MODE_ENABLE": "DMA_FIFOMODE_ENABLE",
}


def _check_hal_macro_misuse(code: str) -> list[str]:
    """Catch HAL macros used with the wrong argument shape - errors the C
    compiler would reject, reported here so the LLM retry loop fixes them
    before a wasted PlatformIO compile cycle."""
    problems = []
    # __HAL_LINKDMA applies & to the handle internally, so the third
    # argument must be the DMA handle VARIABLE, not its address; and the
    # second argument must be the peripheral's DMA-handle FIELD
    # (hdma[TIM_DMA_ID_UPDATE], hdmarx, hdmatx, DMA_Handle, ...), never a
    # bare ALL-CAPS request-ID macro.
    for match in re.finditer(r"__HAL_LINKDMA\s*\(([^)]*)\)", code):
        args = [a.strip() for a in match.group(1).split(",")]
        if len(args) != 3:
            continue
        if re.fullmatch(r"[A-Z][A-Z0-9_]+", args[1]):
            problems.append(
                f"calls __HAL_LINKDMA({args[0]}, {args[1]}, {args[2]}) - the "
                f"second argument must be the peripheral's DMA-handle field, "
                f"not the bare request-ID macro: for a timer it is "
                f"hdma[{args[1]}] (i.e. __HAL_LINKDMA({args[0]}, "
                f"hdma[{args[1]}], {args[2].lstrip('&').strip()}))"
            )
        if args[2].startswith("&"):
            problems.append(
                f"calls __HAL_LINKDMA({args[0]}, {args[1]}, {args[2]}) - the "
                f"macro applies & to the DMA handle itself, so the third "
                f"argument must be the handle variable without '&': "
                f"__HAL_LINKDMA({args[0]}, {args[1]}, "
                f"{args[2].lstrip('&').strip()})"
            )
    return problems


def _check_dma_usage(code: str) -> list[str]:
    """Catch DMA wiring that can never work, even when it compiles."""
    problems = []
    if "HAL_TIM_Base_Start_DMA" in code and "BSRR" in code:
        problems.append(
            "calls HAL_TIM_Base_Start_DMA() while driving GPIO ->BSRR: that "
            "HAL call streams the buffer into the timer's TIMx->DMAR register, "
            "not to a GPIO. To stream a pattern into GPIOx->BSRR from a timer "
            "update event, call HAL_DMA_Start(&hdma_..., (uint32_t)pattern, "
            "(uint32_t)&GPIOx->BSRR, count) on the linked DMA handle, then "
            "enable the timer request with __HAL_TIM_ENABLE_DMA(&htimx, "
            "TIM_DMA_UPDATE) and start the counter with __HAL_TIM_ENABLE(&htimx)"
        )
    if re.search(r"->PAR\s*=", code) and re.search(
        r"HAL_DMA_Start|HAL_\w+_Start_DMA", code
    ):
        problems.append(
            "programs the DMA peripheral-address register (->PAR = ...) "
            "manually while also using a HAL start call - HAL_DMA_Start*() and "
            "HAL_<PERIPH>_*_Start_DMA() set the transfer addresses themselves; "
            "remove the manual ->PAR write"
        )
    # HAL_DMA_Start_IT / HAL_<PERIPH>_*_Start_IT enable the peripheral's
    # interrupt flags, which are useless (or fire lost interrupts) unless the
    # NVIC line is enabled and an IRQ handler exists. If the code uses _IT
    # starts but wires no matching NVIC IRQ + handler, it should either add
    # them or use the blocking-free polling variant (HAL_DMA_Start etc.).
    it_start = re.search(r"HAL_DMA_Start_IT|HAL_\w+_\w*_Start_IT", code)
    if it_start:
        has_nvic = re.search(r"HAL_NVIC_EnableIRQ\s*\(\s*\w*(DMA|TIM|USART|UART|SPI|I2C|ADC)\w*_IRQn", code)
        has_handler = re.search(r"void\s+\w*(DMA\d+_Stream\d+|TIM\d+|USART\d+|UART\d+|SPI\d+|I2C\d+|ADC\w*)_IRQHandler\s*\(", code)
        if not (has_nvic and has_handler):
            problems.append(
                f"calls {it_start.group(0)}() without enabling a matching NVIC "
                "IRQ line and defining its <name>_IRQHandler calling the HAL "
                "handler - either add HAL_NVIC_SetPriority/_EnableIRQ and the "
                "IRQHandler, or use the non-interrupt variant (e.g. "
                "HAL_DMA_Start) since no completion callback is needed"
            )
    return problems


def validate_c_source(code: str, required_features: list[str] | None = None) -> list[str]:
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
    problems.extend(_check_hal_macro_misuse(code))
    problems.extend(_check_dma_usage(code))
    for feature in required_features or []:
        tokens = FEATURE_TOKENS.get(feature)
        if tokens and not any(token in code for token in tokens):
            problems.append(
                f"the request asks for {feature}, but the code does not "
                f"configure or use any {feature} peripheral"
            )
        if feature in PIN_MUX_REQUIRED_FEATURES and not any(
            token in code for token in PIN_MUX_TOKENS
        ):
            problems.append(
                f"the request asks for {feature}, but no GPIO alternate-function "
                f"pin configuration is present - on STM32F4 a {feature} peripheral "
                f"cannot reach its pins unless they are muxed (GPIO_AF / "
                f"GPIO_MODE_AF, or pin setup in a HAL_*_MspInit callback)"
            )
    return problems
