"""
power.py — Server power control using the relay on GPIO22.

Provides:
  - power_on()   — boot the server if it's off
  - power_off()  — graceful or forced shutdown
  - reboot()     — power cycle
  - press()      — raw momentary press (low-level escape hatch)
"""

import time
import logging

from .pins import pulse_relay, pulse_relay_async, is_server_on
from .monitor import ServerState, current_state, ping

logger = logging.getLogger(__name__)

# Pulse durations (seconds)
PRESS_BOOT_S    = 0.5   # Short press → power on / wake
PRESS_FORCE_S   = 5.5   # Long press  → force off (hold power button)


# ── Public API ─────────────────────────────────────────────────────────────────

def press(duration_s: float = PRESS_BOOT_S) -> None:
    """Raw relay pulse. Use power_on/power_off unless you know what you're doing."""
    pulse_relay(duration_s)


def power_on(
    wait_for_boot: bool = True,
    host: str | None = None,
    boot_timeout_s: float = 120.0,
    poll_s: float = 3.0,
) -> bool:
    """
    Power on the server with a short relay pulse.

    Returns True when the server is confirmed on (or immediately if
    wait_for_boot=False). Returns False if timeout exceeded.

    Args:
        wait_for_boot:    Block until power LED is on (and ping OK if host given).
        host:             If provided, also wait for ping to succeed.
        boot_timeout_s:   Max seconds to wait for boot.
        poll_s:           Polling interval while waiting.
    """
    if is_server_on():
        logger.info("power_on: server is already on, skipping")
        return True

    logger.info("power_on: sending boot pulse")
    pulse_relay(PRESS_BOOT_S)

    if not wait_for_boot:
        return True

    deadline = time.monotonic() + boot_timeout_s
    logger.info(f"power_on: waiting for boot (timeout={boot_timeout_s}s)")

    while time.monotonic() < deadline:
        power_up = is_server_on()
        if power_up:
            if host is None:
                logger.info("power_on: power LED is on ✓")
                return True
            if ping(host):
                logger.info(f"power_on: server is up and reachable at {host} ✓")
                return True
        time.sleep(poll_s)

    logger.error("power_on: timeout waiting for server to boot")
    return False


def power_off(
    force: bool = False,
    wait_for_off: bool = True,
    off_timeout_s: float = 60.0,
    poll_s: float = 2.0,
) -> bool:
    """
    Power off the server.

    Args:
        force:          If True, hold relay for 5.5 s (hard power-off).
                        If False, short press (ACPI soft-off / OS shutdown).
        wait_for_off:   Block until power LED goes off.
        off_timeout_s:  Max seconds to wait.
        poll_s:         Polling interval.
    """
    if not is_server_on():
        logger.info("power_off: server is already off, skipping")
        return True

    duration = PRESS_FORCE_S if force else PRESS_BOOT_S
    action = "force-off" if force else "soft-off"
    logger.info(f"power_off: sending {action} pulse ({duration}s)")
    pulse_relay(duration)

    if not wait_for_off:
        return True

    deadline = time.monotonic() + off_timeout_s
    logger.info(f"power_off: waiting for shutdown (timeout={off_timeout_s}s)")

    while time.monotonic() < deadline:
        if not is_server_on():
            logger.info("power_off: power LED is off ✓")
            return True
        time.sleep(poll_s)

    logger.error("power_off: timeout waiting for server to shut down")
    return False


def reboot(
    host: str | None = None,   # kept for compatibility, ignored
    force_off: bool = False,
    off_timeout_s: float = 60.0,
    boot_timeout_s: float = 120.0,  # ignored
) -> bool:
    """
    Power cycle: turn off then on.

    Returns True if power cycle was triggered successfully.
    Does NOT wait for OS boot or ping.
    """
    logger.info("reboot: starting power cycle")

    off_ok = power_off(force=force_off, wait_for_off=True, off_timeout_s=off_timeout_s)
    if not off_ok:
        logger.error("reboot: failed to power off, aborting reboot")
        return False

    # Small gap between off and on
    time.sleep(2.0)

    # Just trigger power ON — no waiting, no ping
    logger.info("reboot: triggering power on (no boot wait)")
    pulse_relay(PRESS_BOOT_S)

    logger.info("reboot: power cycle triggered ✓")
    return True
