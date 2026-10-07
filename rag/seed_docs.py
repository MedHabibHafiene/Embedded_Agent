"""
Canonical hardware facts for the STM32F4-Discovery board.

These are always present in the vector store (the retriever seeds them into an
empty database automatically, and `python -m rag.ingest` includes them on every
rebuild). Keep each fact self-contained - it is embedded as a single chunk.
"""

CORE_HARDWARE_FACTS = [
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
    "Any UART, SPI, or I2C peripheral on STM32F4 requires its pins to be "
    "configured as alternate function (GPIO_MODE_AF_PP or GPIO_MODE_AF_OD with "
    "the correct GPIO_AFx_YYY macro, e.g. GPIO_AF7_USART2 for USART2 on PA2/PA3) "
    "plus the peripheral clock enable. Initializing the peripheral handle alone "
    "is not enough - without AF muxing its signals never reach the pins.",
    "PWM/timer mapping of the disco_f407vg user LEDs (STM32F4-Discovery): "
    "PD12 (green) = TIM4_CH1, PD13 (orange) = TIM4_CH2, PD14 (red) = TIM4_CH3, "
    "PD15 (blue) = TIM4_CH4 - all on alternate function AF2 (GPIO_AF2_TIM4). "
    "When dimming the red LED use TIM4 channel 3, and the blue LED TIM4 "
    "channel 4; channel 1 and 2 map to the green and orange LEDs instead. "
    "The channel passed to HAL_TIM_PWM_ConfigChannel, HAL_TIM_PWM_Start and "
    "__HAL_TIM_SET_COMPARE must be the one wired to the chosen pin.",
    "On STM32F4, PWM on a pin is only possible through the timer and channel "
    "that pin's alternate-function map actually connects to. Before writing "
    "PWM code for a named pin, derive the timer and channel from the AF map "
    "and configure that exact channel - never pick a channel independently "
    "of the pin.",
    "STM32F4 EXTI interrupt vectors are grouped by pin number, not by port: "
    "EXTI lines 0, 1, 2, 3, 4 each have their own IRQ (EXTI0_IRQn..EXTI4_IRQn "
    "with handlers EXTI0_IRQHandler..EXTI4_IRQHandler); lines 5-9 share "
    "EXTI9_5_IRQn (handler EXTI9_5_IRQHandler); lines 10-15 share "
    "EXTI15_10_IRQn (handler EXTI15_10_IRQHandler). The HAL_NVIC_SetPriority / "
    "HAL_NVIC_EnableIRQ argument must be the group of the pin's line - e.g. a "
    "button on PC13 uses EXTI15_10_IRQn, not EXTI13_IRQn.",
    "disco_f407vg (STM32F4-Discovery) user button B1 is on PA0 and is active "
    "high: pressing it drives PA0 to 3.3V, releasing leaves it floating. The "
    "standard configuration is GPIO_MODE_IT_RISING with GPIO_NOPULL and "
    "EXTI0_IRQn. Software debouncing belongs in HAL_GPIO_EXTI_Callback using "
    "HAL_GetTick().",
    "STM32F407 PLL recipe (HSE = 8 MHz on disco_f407vg): choose PLLM so the "
    "VCO input = HSE/PLLM is 1..2 MHz (PLLM=8 gives 1 MHz); choose PLLN so "
    "the VCO output = VCO input x PLLN is 100..432 MHz; SYSCLK = VCO "
    "output/PLLP must stay <= 168 MHz. Examples: 168 MHz = M8/N336/P2 (VCO "
    "336 MHz); 60 MHz = M8/N120/P2 (VCO 120 MHz). PLLQ must divide the VCO "
    "output to <= 48 MHz (exactly 48 MHz when USB or SDIO is used).",
    "STM32F407 flash latency (3.3V) for HAL_RCC_ClockConfig: FLASH_LATENCY_0 "
    "up to 30 MHz HCLK, FLASH_LATENCY_1 up to 60 MHz, FLASH_LATENCY_2 up to "
    "90 MHz, FLASH_LATENCY_3 up to 120 MHz, FLASH_LATENCY_4 up to 150 MHz, "
    "FLASH_LATENCY_5 up to 168 MHz. A slower setting than required is safe "
    "but wastes flash throughput; a faster one crashes randomly.",
    "STM32F4 APB1 timers (TIM2-TIM7, TIM12-TIM14) run at 2x PCLK1 whenever "
    "the APB1 prescaler is not 1. Example for SYSCLK = 60 MHz with "
    "APB1CLKDivider = RCC_HCLK_DIV2: PCLK1 = 30 MHz but the TIM4 counter "
    "clock is 60 MHz, so Prescaler = 60 gives a 1 MHz counting rate.",
    "True 100% PWM duty on STM32F4 edge-aligned up-counting PWM1 mode "
    "requires compare >= Period+1 (e.g. ARR = Period-1 = 999, compare = "
    "1000); compare = Period yields only 99.9%. Compare = 0 gives a solid "
    "0% (output low).",
    "Linking DMA to a timer on STM32F4 HAL: TIM_HandleTypeDef has NO field "
    "like .DMA_UP - that name does not exist and will not compile. The handle "
    "holds a DMA_HandleTypeDef *hdma[7] array; attach a DMA stream with "
    "__HAL_LINKDMA(&htim4, hdma[TIM_DMA_ID_UPDATE], hdma_tim4_up) after "
    "HAL_DMA_Init, enable the update DMA request with __HAL_TIM_ENABLE_DMA("
    "&htim4, TIM_DMA_UPDATE), and enable the counter with __HAL_TIM_ENABLE"
    "(&htim4). On STM32F407, TIM4 update DMA is DMA1 Stream6 Channel2; its "
    "interrupt vector is DMA1_Stream6_IRQn with handler "
    "DMA1_Stream6_IRQHandler.",
    "HAL_DMA_Init fields on STM32F4: the FIFO control field of "
    "DMA_InitTypeDef is named FIFOMode and the valid macros are "
    "DMA_FIFOMODE_ENABLE / DMA_FIFOMODE_DISABLE (DMA_FIFO_MODE_ENABLE / "
    "DMA_FIFO_MODE_DISABLE do NOT exist and will not compile). With "
    "DMA_FIFOMODE_DISABLE the stream runs in direct mode and FIFOThreshold "
    "is ignored.",
    "TIM DMA burst vs GPIO streaming on STM32F4: HAL_TIM_Base_Start_DMA / "
    "HAL_TIM_PWM_Start_DMA always send the buffer into the timer's "
    "TIMx->DMAR - they CANNOT deliver a pattern to a GPIO port. To stream an "
    "on/off pattern from memory into GPIOx->BSRR on every timer update "
    "event, start the DMA channel directly instead: call HAL_DMA_Start("
    "&hdma_tim4_up, (uint32_t)pattern, (uint32_t)&GPIOD->BSRR, count), then "
    "__HAL_TIM_ENABLE_DMA(&htim4, TIM_DMA_UPDATE) and __HAL_TIM_ENABLE("
    "&htim4).",
    "Detecting how LONG the disco_f407vg button is held (e.g. 'after the "
    "button is held for two seconds') needs BOTH edges, not just rising: "
    "GPIO_MODE_IT_RISING alone never fires the callback on release, so a "
    "HAL_GPIO_ReadPin() else-branch for 'released' is dead code. Configure "
    "GPIO_MODE_IT_RISING_FALLING with GPIO_NOPULL (the board already has an "
    "external pull-down), record press_tick = HAL_GetTick() on the rising "
    "edge, then in the main loop trigger the action the moment "
    "HAL_GetTick() - press_tick reaches the hold time WHILE the button is "
    "still held (not when it is released), and clear the press state on the "
    "falling edge.",
    "To set or clear GPIO pins without disturbing the other pins of the same "
    "port, write GPIOx->BSRR (lower 16 bits SET the pin, upper 16 bits RESET "
    "it). Never DMA into GPIOx->ODR for a subset of pins: ODR is a plain "
    "32-bit data register, so a value like (1<<12)|(1<<15) also forces every "
    "other GPIOD output LOW.",
    "A real 'DMA blink' on STM32F4 does NOT call HAL_DMA_Start + HAL_Delay "
    "in a loop - that is CPU-timed and DMA is just a memory copy. The "
    "proper pattern is timer-triggered DMA: put the DMA handle in "
    "DMA_CIRCULAR mode, link it with __HAL_LINKDMA, then ONE call to "
    "HAL_DMA_Start(&hdma_tim4_up, (uint32_t)pattern, (uint32_t)&GPIOD->BSRR, "
    "count) followed by __HAL_TIM_ENABLE_DMA(&htim4, TIM_DMA_UPDATE) and "
    "__HAL_TIM_ENABLE(&htim4) makes the timer update events push each "
    "pattern element to GPIOD->BSRR with zero CPU involvement - no "
    "HAL_Delay, and the CPU is free from then on.",
]
