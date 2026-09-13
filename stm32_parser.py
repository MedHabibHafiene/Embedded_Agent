import re
from typing import Dict, Any, List

def parse_stm32_code(c_code: str) -> Dict[str, Any]:
    """
    Static C-code parser to extract STM32 CubeHAL configurations into JSON.
    Uses regex to identify GPIO, Clock, and Peripheral settings.
    """
    state = {
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
    # Matches: RCC_OscInitStruct.PLL.PLLN = 168;
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

    # Calculate SYSCLK (Simplified for HSI/PLL)
    try:
        pllm = int(state["clock"].get("PLLM", 0))
        plln = int(state["clock"].get("PLLN", 0))
        pllp_token = state["clock"].get("PLLP", "")
        pllp_divider = int(re.search(r"DIV(\d+)", pllp_token).group(1))
        # HSI is 16MHz for F407
        if pllm and plln:
            state["clock"]["sysclk_mhz"] = (16 * plln) // (pllm * pllp_divider)
    except (AttributeError, ValueError, ZeroDivisionError):
        state["clock"]["sysclk_mhz"] = "Unknown"

    # 3. Peripherals & Timers
    # Matches: MX_TIM3_Init, MX_USART1_Init
    peripheral_inits = re.findall(r"void\s+(MX_\w+_Init)\s*\(\s*\)", c_code)
    for init in peripheral_inits:
        name = init.replace("MX_", "").replace("_Init", "")
        # Extract specific values within the function body
        start_idx = c_code.find(init)
        end_idx = c_code.find("}", start_idx)
        body = c_code[start_idx:end_idx]
        
        params = {}
        # Common HAL params
        for param in ["Prescaler", "Period", "BaudRate", "CounterMode", "DutyCycle"]:
            p_match = re.search(rf"\.{param}\s*=\s*([^;]+);", body)
            if p_match:
                params[param] = p_match.group(1).strip()
        
        state["peripherals"][name] = params

    return state
