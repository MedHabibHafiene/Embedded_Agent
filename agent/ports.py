"""
Serial port handling for the board connection.

The upload port is NEVER guessed anymore. The old code silently fell back
to "COM5" (Windows) / "dev/ttyACM0" (POSIX) when nothing was configured,
which only worked by accident. Now:

  explicit configuration only:
    request/CLI argument  >  PLATFORMIO_UPLOAD_PORT (env or .env)

  nothing configured      ->  platformio.ini is written WITHOUT upload_port,
                              and PlatformIO's own auto-detection decides.

list_serial_ports() exposes what's actually attached (for GET /api/ports,
verbose health, and the CLI) - choosing a port is a user decision, not a
guess.
"""

import logging
import re

from config import PLATFORMIO_UPLOAD_PORT

log = logging.getLogger(__name__)


def list_serial_ports() -> list[dict]:
    """All serial ports currently visible, with identifying metadata."""
    import serial.tools.list_ports

    ports = []
    for p in serial.tools.list_ports.comports():
        hwid = getattr(p, "hwid", "") or ""
        desc = getattr(p, "description", "") or ""
        ports.append({
            "device": getattr(p, "device", None),
            "description": desc,
            "hwid": hwid,
            "st_link": bool(re.search(r"STMicroelectronics|ST-Link|0483:001[0-9]", hwid + " " + desc, re.I)),
        })
    return ports


def detect_stlink_port() -> str | None:
    """Return the COM/tty port of an ST-LINK adaptor, or None. Works cross-platform."""
    for port in list_serial_ports():
        if port["st_link"]:
            return port["device"]
    return None


def resolve_upload_port(explicit: str | None = None) -> str | None:
    """Explicit configuration only - never a hardcoded fallback.

    Order: caller-supplied value (API request / CLI flag)
           > PLATFORMIO_UPLOAD_PORT (env var or .env)
           > None (let PlatformIO auto-detect).
    """
    port = (explicit or "").strip() or None
    if port:
        log.info("Using explicitly configured upload port: %s", port)
        return port
    if PLATFORMIO_UPLOAD_PORT:
        log.info("Using upload port from PLATFORMIO_UPLOAD_PORT: %s", PLATFORMIO_UPLOAD_PORT)
        return PLATFORMIO_UPLOAD_PORT
    detected = detect_stlink_port()
    if detected:
        log.info("No port configured; ST-LINK detected on %s.", detected)
        return detected
    log.info(
        "No upload port configured - PlatformIO will auto-detect. "
        "Set one via the API request, CLI --port, or PLATFORMIO_UPLOAD_PORT."
    )
    return None
