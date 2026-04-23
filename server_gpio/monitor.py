"""
monitor.py — Server state monitoring.

Provides:
  - ServerState enum
  - current_state()   — single snapshot
  - watch()           — generator that yields state changes
  - is_hung()         — detects a crashed/hung server
"""

import time
import subprocess
import logging
from enum import Enum, auto
from collections.abc import Generator

from .pins import is_server_on, is_ssd_active

logger = logging.getLogger(__name__)


# ── State model ────────────────────────────────────────────────────────────────

class ServerState(Enum):
    ON      = auto()   # Power LED on, ping OK
    OFF     = auto()   # Power LED off
    HUNG    = auto()   # Power LED on, ping failing
    BOOTING = auto()   # Power LED on, ping not yet up (transient)
    UNKNOWN = auto()   # Can't determine


# ── Ping helper ────────────────────────────────────────────────────────────────

def ping(host: str, count: int = 2, timeout_s: int = 2) -> bool:
    """Returns True if `host` responds to ICMP ping."""
    try:
        result = subprocess.run(
            ["ping", "-c", str(count), "-W", str(timeout_s), host],
            capture_output=True, timeout=timeout_s * count + 2
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


# ── State snapshot ─────────────────────────────────────────────────────────────

def current_state(
    host: str | None = None,
    ping_required: bool = True,
    boot_grace_s: float = 30.0,
    _boot_start: float | None = None,
) -> ServerState:
    """
    Return the current ServerState.

    Args:
        host:           IP/hostname to ping. If None, ping check is skipped
                        and ON/HUNG distinction is not made.
        ping_required:  If False, skip ping and report ON whenever LED is on.
        boot_grace_s:   Seconds after power-on before we start expecting pings.
        _boot_start:    Internal — timestamp when booting started (for grace period).
    """
    power_on = is_server_on()

    if not power_on:
        return ServerState.OFF

    # Power LED is on
    if not ping_required or host is None:
        return ServerState.ON

    # Within boot grace period?
    if _boot_start and (time.monotonic() - _boot_start) < boot_grace_s:
        return ServerState.BOOTING

    reachable = ping(host)
    if reachable:
        return ServerState.ON
    else:
        return ServerState.HUNG


# ── Continuous watcher ─────────────────────────────────────────────────────────

def watch(
    host: str | None = None,
    poll_interval_s: float = 5.0,
    ping_required: bool = True,
    boot_grace_s: float = 30.0,
) -> Generator[tuple[ServerState, ServerState], None, None]:
    """
    Generator that yields (old_state, new_state) whenever the server state changes.

    Usage:
        for old, new in monitor.watch(host="192.168.1.10"):
            print(f"State changed: {old} → {new}")

    Args:
        host:            IP/hostname for ping checks.
        poll_interval_s: How often to poll GPIO + ping.
        ping_required:   Include ping in ON/HUNG detection.
        boot_grace_s:    Grace period after a detected boot before expecting pings.
    """
    boot_start: float | None = None
    last_state = ServerState.UNKNOWN

    while True:
        state = current_state(
            host=host,
            ping_required=ping_required,
            boot_grace_s=boot_grace_s,
            _boot_start=boot_start,
        )

        # Track boot start time for grace period
        if last_state in (ServerState.OFF, ServerState.UNKNOWN) and state == ServerState.BOOTING:
            boot_start = time.monotonic()
        elif state == ServerState.ON:
            boot_start = None  # fully up, reset

        if state != last_state:
            logger.info(f"Server state: {last_state.name} → {state.name}")
            yield last_state, state
            last_state = state

        time.sleep(poll_interval_s)


# ── Hang detector ──────────────────────────────────────────────────────────────

def is_hung(
    host: str,
    power_on_required: bool = True,
    ping_retries: int = 3,
    ping_timeout_s: int = 2,
) -> bool:
    """
    Returns True if the server appears to be hung:
      - Power LED is ON (server hasn't cleanly shut down)
      - Ping fails after `ping_retries` attempts

    Args:
        host:               IP/hostname to ping.
        power_on_required:  If True, returns False when power LED is off
                            (that's just a clean shutdown, not a hang).
        ping_retries:       Number of consecutive ping failures before declaring hung.
        ping_timeout_s:     Seconds to wait per ping.
    """
    if power_on_required and not is_server_on():
        return False  # server is cleanly off

    for attempt in range(ping_retries):
        if ping(host, count=1, timeout_s=ping_timeout_s):
            return False
        logger.debug(f"Ping failed ({attempt + 1}/{ping_retries})")
        time.sleep(1)

    logger.warning(f"Server appears hung: power LED on but {host} unreachable after {ping_retries} pings")
    return True
