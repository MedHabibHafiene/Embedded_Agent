"""
Analysis: static C-code parser extracting STM32 CubeHAL configuration into a
hardware-state JSON (was the top-level stm32_parser.py).

The state feeds the frontend's "hardware state" visualization: GPIO pins,
clock tree, and peripheral parameters. Purely regex-based - it intentionally
does not need a real C parser.
"""

import re
from typing import Any, Dict


def _expand_gpio_aliases(c_code: str) -> str:
    """Resolve simple GPIO alias macros so regex matching sees real tokens:

        #define LED_GREEN_PORT GPIOD
        #define LED_GREEN_PIN  GPIO_PIN_12

    CubeMX-style and LLM-generated code frequently uses such aliases; without
    expansion, HAL_GPIO_Init(LED_GREEN_PORT, ...) is invisible to the parser.
    """
    resolved = c_code
    for name, value in re.findall(r"#define\s+(\w+)\s+([^/\n]+)", c_code):
        value = value.strip()
        if re.search(r"\bGPIO[A-K]\b|\bGPIO_PIN_\d+\b", value):
            # lambda avoids interpreting backslashes/escapes in the value
            resolved = re.sub(rf"\b{name}\b", lambda _m: value, resolved)
    return resolved


def parse_stm32_code(c_code: str) -> Dict[str, Any]:
    """Parse generated HAL C code into a GPIO/clock/peripheral state dict."""
    c_code = _expand_gpio_aliases(c_code)

    state: Dict[str, Any] = {
        "gpio": [],
        "clock": {},
        "peripherals": {}
    }

    # Find all HAL_GPIO_Init calls and look backwards for the nearest GPIO_InitTypeDef.
    init_calls = list(re.finditer(r"HAL_GPIO_Init\((GPIO[A-K]),\s*&(\w+)\);", c_code))
    for call in init_calls:
        port = call.group(1)
        struct_name = call.group(2)

        # Restrict assignments to this initialization block so multiple GPIO
        # ports do not inherit values from an earlier block.
        previous_call = c_code.rfind("HAL_GPIO_Init(", 0, call.start())
        declaration_start = c_code.rfind("GPIO_InitTypeDef", 0, call.start())
        block_start = max(previous_call, declaration_start)
        if previous_call >= 0:
            block_start = c_code.find(";", previous_call) + 1
        struct_def = c_code[block_start:call.start()]
        pin_match = re.search(rf"{struct_name}\.Pin\s*=\s*([^;]+);", struct_def)
        mode_match = re.search(rf"{struct_name}\.Mode\s*=\s*([^;]+);", struct_def)
        pull_match = re.search(rf"{struct_name}\.Pull\s*=\s*([^;]+);", struct_def)
        speed_match = re.search(rf"{struct_name}\.Speed\s*=\s*([^;]+);", struct_def)

        pin_expression = pin_match.group(1).strip() if pin_match else "Unknown"
        pin_numbers = re.findall(r"GPIO_PIN_(\d+)", pin_expression)
        pin_names = [f"{port[4:]}{number}" for number in pin_numbers]
        state["gpio"].append({
            "port": port,
            "pins": pin_expression,
            "pin_names": pin_names,
            "mode": mode_match.group(1).strip() if mode_match else "Unknown",
            "pull": pull_match.group(1).strip() if pull_match else "Unknown",
            "speed": speed_match.group(1).strip() if speed_match else "Unknown",
        })

    # 2. Extract Clock Tree
    clock_patterns = {
        "source": r"RCC_OscInitStruct\.OscillatorType\s*=\s*([^;]+);",
        "PLLM": r"RCC_OscInitStruct\.PLL\.PLLM\s*=\s*(\d+);",
        "PLLN": r"RCC_OscInitStruct\.PLL\.PLLN\s*=\s*(\d+);",
        "PLLP": r"RCC_OscInitStruct\.PLL\.PLLP\s*=\s*([^;]+);",
        "PLLQ": r"RCC_OscInitStruct\.PLL\.PLLQ\s*=\s*(\d+);",
        "AHBPrescaler": r"RCC_ClkInitStruct\.AHBCLKDivider\s*=\s*([^;]+);",
        "APB1Prescaler": r"RCC_ClkInitStruct\.APB1CLKDivider\s*=\s*([^;]+);",
        "APB2Prescaler": r"RCC_ClkInitStruct\.APB2CLKDivider\s*=\s*([^;]+);",
    }

    for key, pattern in clock_patterns.items():
        match = re.search(pattern, c_code)
        state["clock"][key] = match.group(1).strip() if match else "Unknown"

    # Compute the real clock tree (STM32F407):
    #   PLL_IN = SRC / PLLM          (SRC = HSE 8 MHz crystal or HSI 16 MHz)
    #   VCO    = PLL_IN * PLLN
    #   SYSCLK = VCO / PLLP          (max 168 MHz)
    #   HCLK   = SYSCLK / AHB_DIV
    #   PCLK1  = HCLK / APB1_DIV     (max 42 MHz)
    #   PCLK2  = HCLK / APB2_DIV     (max 84 MHz)
    #   USB    = VCO / PLLQ          (48 MHz for USB/SDIO/RNG)
    try:
        pllm = int(state["clock"].get("PLLM", 0) or 0)
        plln = int(state["clock"].get("PLLN", 0) or 0)

        def _div(token, default=1):
            """Divider from tokens like RCC_SYSCLK_DIV1 / RCC_HCLK_DIV4 / 2."""
            m = re.search(r"DIV(\d+)", str(token))
            return int(m.group(1)) if m else default

        osc_type = str(state["clock"].get("source", ""))
        pll_src = re.search(r"PLL\.PLLSource\s*=\s*(\w+)", c_code)
        uses_hse = "HSE" in osc_type or bool(pll_src and "HSE" in pll_src.group(1))
        src_mhz = 8 if uses_hse else 16  # Discovery boards carry an 8 MHz HSE crystal

        pllp_token = str(state["clock"].get("PLLP", ""))
        m = re.search(r"DIV(\d+)", pllp_token)
        pllp = int(m.group(1)) if m else (int(pllp_token) if pllp_token.isdigit() else 1)

        warnings = []
        if pllm and plln and pllp:
            pll_in = src_mhz / pllm
            vco = pll_in * plln
            sysclk = vco / pllp
            state["clock"]["pll_vco_mhz"] = round(vco, 1)
            state["clock"]["sysclk_mhz"] = round(sysclk)

            ahb = _div(state["clock"].get("AHBPrescaler"))
            apb1 = _div(state["clock"].get("APB1Prescaler"))
            apb2 = _div(state["clock"].get("APB2Prescaler"))
            hclk = sysclk / ahb
            pclk1 = hclk / apb1
            pclk2 = hclk / apb2
            state["clock"]["hclk_mhz"] = round(hclk)
            state["clock"]["pclk1_mhz"] = round(pclk1)
            state["clock"]["pclk2_mhz"] = round(pclk2)

            pllq = state["clock"].get("PLLQ", "")
            if str(pllq).isdigit() and int(pllq):
                state["clock"]["usb_mhz"] = round(vco / int(pllq), 1)

            if sysclk > 168:
                warnings.append(f"SYSCLK {sysclk:g} MHz exceeds the 168 MHz F407 limit")
            if pclk1 > 42:
                warnings.append(
                    f"PCLK1 {pclk1:g} MHz exceeds the 42 MHz APB1 limit - "
                    f"use RCC_HCLK_DIV{max(4, -(-int(hclk) // 42))} at {hclk:g} MHz HCLK"
                )
            if pclk2 > 84:
                warnings.append(
                    f"PCLK2 {pclk2:g} MHz exceeds the 84 MHz APB2 limit - "
                    f"use RCC_HCLK_DIV{max(2, -(-int(hclk) // 84))} at {hclk:g} MHz HCLK"
                )
        else:
            state["clock"]["sysclk_mhz"] = "Unknown"
        state["clock"]["warnings"] = warnings
    except (AttributeError, ValueError, ZeroDivisionError):
        state["clock"]["sysclk_mhz"] = "Unknown"
        state["clock"]["warnings"] = []

    # 3. Peripherals & Timers
    # Accept both () and (void) signatures - CubeMX emits (void).
    peripheral_inits = list(set(re.findall(r"void\s+(MX_\w+_Init)\s*\(\s*(?:void)?\s*\)", c_code)))
    for init in peripheral_inits:
        name = init.replace("MX_", "").replace("_Init", "")
        # Use the definition (last occurrence, after main), not the prototype,
        # and stop at a column-0 closing brace so nested blocks don't cut the
        # extraction short.
        start_idx = c_code.rfind(init)
        end_idx = c_code.find("\n}", start_idx)
        body = c_code[start_idx:end_idx]

        params = {}
        # Common HAL params
        for param in ["Prescaler", "Period", "BaudRate", "CounterMode", "DutyCycle"]:
            p_match = re.search(rf"\.{param}\s*=\s*([^;]+);", body)
            if p_match:
                params[param] = p_match.group(1).strip()

        state["peripherals"][name] = params

    return state
