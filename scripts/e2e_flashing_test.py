"""End-to-end pipeline test: generate (with compile verification) and flash
one firmware example per use case against the running API server.

Run with the server up:  venv/Scripts/python.exe scripts/e2e_flashing_test.py
Results are appended to scripts/e2e_flashing_results.log
"""

import json
import sys
import time
from pathlib import Path

import requests

API = "http://localhost:8000"
LOG = Path(__file__).with_name("e2e_flashing_results.log")

USE_CASES = [
    ("gpio_blink", "Blink the green LED every second."),
    (
        "multi_led_cycle",
        "Cycle through all four user LEDs (green, orange, red, blue), "
        "turning each on for 250 ms one after another in a loop.",
    ),
    (
        "button_exti",
        "Toggle the red LED every time the user button is pressed, using an "
        "external interrupt with software debounce.",
    ),
    (
        "pwm_fade",
        "Fade the blue LED smoothly up and down using PWM.",
    ),
    (
        "uart_hello",
        "Send 'Hello STM32' over UART2 at 115200 baud every two seconds.",
    ),
    (
        "dma_button_hold",
        "Use DMA to blink the green and blue LEDs after the button is held "
        "for two seconds.",
    ),
]


def log_line(line: str) -> None:
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def run_use_case(name: str, prompt: str) -> str:
    started = time.time()
    r = requests.post(
        f"{API}/api/generate",
        json={"prompt": prompt, "verify": True},
        timeout=900,
    )
    elapsed = time.time() - started
    if r.status_code != 200:
        return f"FAIL generate HTTP {r.status_code} after {elapsed:.0f}s: {r.text[:300]}"

    data = r.json()
    build = data.get("build") or {}
    if build.get("status") != "success":
        return f"FAIL compile (generate {elapsed:.0f}s): {build.get('logs', '')[-300:]}"

    started = time.time()
    r = requests.post(
        f"{API}/api/flash",
        json={"id": data["id"], "confirmed": True},
        timeout=300,
    )
    flash_elapsed = time.time() - started
    flash = r.json()
    if flash.get("status") != "success":
        return f"FAIL flash after {flash_elapsed:.0f}s: {flash.get('logs', '')[-300:] or r.text[:300]}"

    return f"OK (generate {elapsed:.0f}s, flash {flash_elapsed:.0f}s, job {data['id']})"


def main(names: list[str]) -> int:
    selected = [uc for uc in USE_CASES if not names or uc[0] in names]
    if not selected:
        print("No matching use cases; available:", ", ".join(n for n, _ in USE_CASES))
        return 2

    log_line(f"\n=== E2E flashing test run ({len(selected)} use cases) ===")
    failures = 0
    for name, prompt in selected:
        log_line(f"--- {name}: '{prompt}'")
        try:
            outcome = run_use_case(name, prompt)
        except Exception as e:  # network/timeout etc.
            outcome = f"ERROR {type(e).__name__}: {e}"
        log_line(f"    {name}: {outcome}")
        if not outcome.startswith("OK"):
            failures += 1
        time.sleep(2)

    log_line(f"=== Done: {len(selected) - failures}/{len(selected)} passed ===")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
