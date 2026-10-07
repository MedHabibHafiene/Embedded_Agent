"""
Builder: everything that touches the PlatformIO project on disk or the board.

  - write generated source into stm32_project/src/, together with a generated
    stm32f4xx_it.c providing the SysTick/EXTI IRQ trampolines the HAL needs
  - maintain platformio.ini (fixed template - no duplicate default_envs)
  - compile / flash via `pio run` with hard timeouts
  - archive flashed firmware into stm32_project/history/

Every failure raises BuildError carrying the full tool output, so callers can
show the real compiler/linker error (PlatformIO reports those on stdout).
"""

import json
import logging
import re
import shutil
import subprocess
import textwrap
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from agent.analysis import parse_stm32_code
from agent.errors import BuildError
from agent.ports import resolve_upload_port
from config import BUILD_TIMEOUT_SEC, STM32_PROJECT_DIR, UPLOAD_TIMEOUT_SEC

log = logging.getLogger(__name__)

# OpenOCD could open the ST-LINK but the target ignored SWD init - a board /
# debugger connection problem that regenerating code can never fix.
OPENOCD_CONNECT_HINT = textwrap.dedent("""\
    OpenOCD could not connect to the target. This is a debugger/board
    connection problem, NOT a firmware problem - the code compiled fine.
    Fix the connection, then flash the same firmware again:
      1. Unplug the board and plug it back in; use a USB port on the PC
         directly (no hub) and a data-capable cable.
      2. Close any other tool holding the ST-LINK (STM32CubeIDE,
         STM32CubeProgrammer, a previous upload, a serial monitor).
      3. Connect under reset: press and HOLD the board's RESET button,
         start the upload, release the button about one second after
         OpenOCD starts.
      4. Still failing: add 'debug_speed = 950' to the [env:disco_f407vg]
         section of platformio.ini, update the on-board ST-LINK firmware
         with STM32CubeProgrammer, and try another USB cable or port.""")


# --------------------------------------------------------------------------
# stm32f4xx_it.c generation: the HAL glue CubeMX normally provides.
#
# Generated firmware implements interrupt logic in HAL callbacks only, so
# the raw IRQ trampolines are supplied here for ANY EXTI line - derived from
# the source itself (pin modes + NVIC enables), never hardcoded. Handlers
# the generated code already defines are skipped, so a source that provides
# its own trampolines never collides.
# --------------------------------------------------------------------------

# EXTI lines share IRQ vectors: 5-9 and 10-15 are grouped on STM32F4.
_EXTI_GROUP_HANDLER_LINES = {
    "EXTI9_5_IRQHandler": range(5, 10),
    "EXTI15_10_IRQHandler": range(10, 16),
}


def _exti_handler_for_line(line: int) -> str:
    """IRQ handler name owning an EXTI line (0-4 individual, 5-9/10-15 shared)."""
    if line <= 4:
        return f"EXTI{line}_IRQHandler"
    if line <= 9:
        return "EXTI9_5_IRQHandler"
    return "EXTI15_10_IRQHandler"


def _strip_c_comments(code: str) -> str:
    """Drop /*...*/ and //... so commented-out code never fakes a symbol."""
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", "", code)


def _configured_exti_lines(source_code: str) -> dict[str, set[int]]:
    """Map IRQ-handler name -> the EXTI lines the firmware actually uses.

    Two independent signals, unioned: pins whose GPIO init block uses a
    GPIO_MODE_IT_* mode (alias macros resolved by the same parser the
    hardware-state viewer uses), and EXTI IRQ numbers passed to
    HAL_NVIC_SetPriority/EnableIRQ. A handler seen only through its IRQn
    gets every line of its group: HAL_GPIO_EXTI_IRQHandler checks the
    actual pending bits, so extra lines are harmless - missing ones are not.
    """
    lines_by_handler: dict[str, set[int]] = {}
    code = _strip_c_comments(source_code)

    for entry in parse_stm32_code(code).get("gpio", []):
        if "GPIO_MODE_IT" not in entry.get("mode", ""):
            continue
        for pin_name in entry.get("pin_names", []):
            line = int(pin_name[1:])
            lines_by_handler.setdefault(_exti_handler_for_line(line), set()).add(line)

    for token in re.findall(r"\bEXTI(15_10|9_5|\d+)_IRQn\b", code):
        if token in ("9_5", "15_10"):
            handler = f"EXTI{token}_IRQHandler"
            lines_by_handler.setdefault(handler, set()).update(
                _EXTI_GROUP_HANDLER_LINES[handler]
            )
        else:
            line = int(token)
            lines_by_handler.setdefault(_exti_handler_for_line(line), set()).add(line)

    return lines_by_handler


def build_interrupt_file(source_code: str) -> str | None:
    """Content of stm32f4xx_it.c for this source, or None if it needs nothing.

    SysTick_Handler is always required unless the source defines it: without
    HAL_IncTick, HAL_GetTick stays at 0 and HAL_Delay/debounce deadlocks.
    """
    code = _strip_c_comments(source_code)
    parts: list[str] = []

    if not re.search(r"\bvoid\s+SysTick_Handler\s*\(", code):
        parts.append(
            "void SysTick_Handler(void)\n"
            "{\n"
            "    HAL_IncTick();\n"
            "    HAL_SYSTICK_IRQHandler();\n"
            "}"
        )

    for handler, lines in sorted(
        _configured_exti_lines(source_code).items(), key=lambda item: min(item[1])
    ):
        if re.search(rf"\bvoid\s+{re.escape(handler)}\s*\(", code):
            continue
        calls = "\n".join(
            f"    HAL_GPIO_EXTI_IRQHandler(GPIO_PIN_{line});" for line in sorted(lines)
        )
        parts.append(f"void {handler}(void)\n{{\n{calls}\n}}")

    if not parts:
        return None
    return '#include "stm32f4xx_hal.h"\n\n' + "\n\n".join(parts) + "\n"


@dataclass
class BuildOutcome:
    """Result of a successful write -> compile -> upload -> archive run."""

    logs: str


def write_source_files(project_dir: Path, source_code: str, filename: str = "main.c") -> None:
    """Write the generated firmware plus the stm32f4xx_it.c HAL trampolines.

    The interrupt file carries SysTick_Handler and whichever EXTI handlers
    this particular source needs (any EXTI line), replacing whatever a
    previous generation left behind so stale handlers can never collide
    with the new code.
    """
    src_dir = project_dir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    out_file = src_dir / filename
    out_file.write_text(source_code, encoding="utf-8")
    log.info("Written firmware to %s", out_file)

    interrupt_file = src_dir / "stm32f4xx_it.c"
    interrupt_content = build_interrupt_file(source_code)
    if interrupt_content is None:
        if interrupt_file.exists():
            interrupt_file.unlink()
            log.info("Removed stale %s - generated code defines all its handlers.",
                     interrupt_file)
        else:
            log.info("%s not needed - generated code defines all its handlers.",
                     interrupt_file)
    else:
        interrupt_file.write_text(interrupt_content, encoding="utf-8")
        log.info("Written %s (HAL tick + EXTI trampolines)", interrupt_file)


def ensure_platformio_ini(project_dir: Path, port: str | None = None) -> None:
    """Write a minimal platformio.ini for the STM32F407 Discovery board.

    The upload/monitor port is written ONLY when one is explicitly
    configured (API request, CLI flag, or PLATFORMIO_UPLOAD_PORT) or an
    ST-LINK is detectable; otherwise the lines are omitted so PlatformIO's
    own auto-detection picks the board - a hardcoded COM5 only worked by
    accident. The [env] section intentionally has no default_envs either.
    """
    ini_path = project_dir / "platformio.ini"
    port = resolve_upload_port(explicit=port)

    if port:
        port_lines = (
            f"upload_port = {port}\n"
            f"monitor_port = {port}\n"
            f"debug_port = {port}\n"
        )
    else:
        port_lines = "; port: auto-detected by PlatformIO\n"

    ini_content = textwrap.dedent(
        f"""\
        [platformio]
        default_envs = disco_f407vg

        [env:disco_f407vg]
        platform = ststm32
        board = disco_f407vg
        framework = stm32cube
        {port_lines}
        [env]

        ; Platform packages
        platform_packages =
            platformio/framework-stm32cubef4@^1.28.3
        """
    ).lstrip("\n")

    if not ini_path.exists() or ini_path.read_text().strip() != ini_content.strip():
        ini_path.write_text(ini_content, encoding="utf-8")
        log.info("Wrote platformio.ini (%s)", f"upload_port={port}" if port else "port auto-detect")


def archive_and_remove_source(project_dir: Path, command: str, status: str = "flashed") -> None:
    """Move the flashed main.c into a timestamped history folder."""
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
    log.info("Archived firmware to %s", history_dir)
    source_file.unlink()
    log.info("Cleaned up %s after flashing.", source_file)


def _run(cmd: list[str], cwd: Path, timeout: int, step: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired as e:
        raise BuildError(
            f"{step} timed out after {timeout}s. "
            f"Check that the board/ST-LINK is connected and not stuck in a bad state."
        ) from e


def compile_firmware(project_dir: Path) -> str:
    log.info("Running 'pio run' (compilation)...")
    result = _run(["pio", "run"], project_dir, BUILD_TIMEOUT_SEC, "Compilation")
    if result.returncode != 0:
        raise BuildError(
            f"Compilation failed (exit {result.returncode}):",
            stdout=result.stdout,
            stderr=result.stderr,
        )
    log.info("Compilation succeeded.")
    return result.stdout


def upload_firmware(project_dir: Path) -> str:
    log.info("Running 'pio run --target upload' (flashing)...")
    result = _run(
        ["pio", "run", "--target", "upload"], project_dir, UPLOAD_TIMEOUT_SEC, "Upload"
    )
    if result.returncode != 0:
        # OpenOCD failing to reach the target reads like a code failure to
        # users (it appears in "build & flash logs") but is purely a
        # connection problem - say so explicitly instead of letting them
        # regenerate working firmware.
        output = f"{result.stdout}\n{result.stderr}".lower()
        if "init mode failed" in output or "openocd init failed" in output:
            raise BuildError(
                f"Upload failed (exit {result.returncode}): {OPENOCD_CONNECT_HINT}",
                stdout=result.stdout,
                stderr=result.stderr,
            )
        raise BuildError(
            f"Upload failed (exit {result.returncode}):",
            stdout=result.stdout,
            stderr=result.stderr,
        )
    log.info("Firmware successfully flashed to the board.")
    return result.stdout


def verify_firmware(
    project_dir: Path,
    source_code: str,
    command: str = "",
) -> BuildOutcome:
    """Compile-check generated code WITHOUT touching any hardware.

    Writes the source into the PlatformIO project, runs `pio run`
    (compile only - never `--target upload`), then archives the source so
    the project directory stays clean. Raises BuildError with the full
    compiler output on failure.
    """
    ensure_platformio_ini(project_dir)
    write_source_files(project_dir, source_code)
    compile_logs = compile_firmware(project_dir)
    archive_and_remove_source(project_dir, command, status="verified")
    return BuildOutcome(logs=compile_logs)


def flash_firmware(
    project_dir: Path,
    source_code: str,
    command: str = "",
    port: str | None = None,
) -> BuildOutcome:
    """Non-interactive path used by the API: write, compile, upload, archive."""
    ensure_platformio_ini(project_dir, port=port)
    write_source_files(project_dir, source_code)
    compile_logs = compile_firmware(project_dir)
    upload_logs = upload_firmware(project_dir)
    archive_and_remove_source(project_dir, command)
    return BuildOutcome(logs=f"{compile_logs}\n{upload_logs}".strip())


def build_and_flash(
    project_dir: Path,
    auto_confirm: bool = False,
    command: str = "",
    port: str | None = None,
) -> None:
    """Interactive path used by the CLI: compile, confirm, upload, archive."""
    ensure_platformio_ini(project_dir, port=port)
    compile_firmware(project_dir)

    if not auto_confirm:
        reply = input(
            "[Build] Firmware compiled. Flash it to the connected board now? [y/N] "
        ).strip().lower()
        if reply != "y":
            log.info("Flash skipped by user.")
            return

    upload_firmware(project_dir)
    archive_and_remove_source(project_dir, command)


__all__ = [
    "archive_and_remove_source",
    "build_and_flash",
    "build_interrupt_file",
    "compile_firmware",
    "ensure_platformio_ini",
    "flash_firmware",
    "upload_firmware",
    "write_source_files",
    "STM32_PROJECT_DIR",
]
