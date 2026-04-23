"""
pins.py — Low-level GPIO abstractions using the gpiod Python library (v2.x).
All reads/writes go through here. Nothing else touches hardware directly.

Confirmed working on:
  Debian Trixie 13.4 | kernel 6.12 | Python 3.13 | gpiod 2.2.0
  gpiochip0 [pinctrl-rp1] — Pi 5 main GPIO chip

Pin logic (LEDs pull lines LOW when lit — logic is inverted):
  GPIO17 INACTIVE = power LED on  = server ON
  GPIO27 INACTIVE = SSD LED on    = SSD busy
  GPIO22 ACTIVE   = relay on      = power button pressed
"""

import time
import threading
import logging

import gpiod
from gpiod.line import Direction, Value, Edge

logger = logging.getLogger(__name__)


# ── Pin configuration ──────────────────────────────────────────────────────────

class PinConfig:
    CHIP      : str = "/dev/gpiochip0"   # pinctrl-rp1 on Pi 5
    POWER_LED : int = 17
    SSD_LED   : int = 27
    RELAY     : int = 22
    CONSUMER  : str = "server_gpio"


# ── Internal read helper ───────────────────────────────────────────────────────

def _read(pin: int) -> Value:
    """Open pin, read once, release. Thread-safe single-shot read."""
    with gpiod.request_lines(
        PinConfig.CHIP,
        consumer=PinConfig.CONSUMER,
        config={pin: gpiod.LineSettings(direction=Direction.INPUT)}
    ) as req:
        return req.get_value(pin)


# ── Semantic readers ───────────────────────────────────────────────────────────

def is_server_on() -> bool:
    """True when the power LED is lit (GPIO17 INACTIVE = LED on = server ON)."""
    return _read(PinConfig.POWER_LED) == Value.INACTIVE


def is_ssd_active() -> bool:
    """True when the SSD LED is lit (GPIO27 INACTIVE = LED on = SSD busy)."""
    return _read(PinConfig.SSD_LED) == Value.INACTIVE


# ── Relay control ─────────────────────────────────────────────────────────────
#
# Problem: on Pi 5 / kernel 6.12, releasing a gpiod OUTPUT line causes it to
# float HIGH. Our relay is active-HIGH so it latches on every release.
#
# Fix (confirmed by hardware test):
#   1. Drive OUTPUT HIGH for the pulse duration
#   2. Release OUTPUT — line floats HIGH, relay latches momentarily
#   3. Immediately open as INPUT — this pulls the line LOW, relay releases
#   4. Release INPUT — line floats LOW (confirmed: no latch after INPUT release)
#
# The INPUT→release sequence is the key: after an INPUT release the line
# settles LOW naturally, whereas after an OUTPUT release it floats HIGH.

_relay_lock = threading.Lock()


def pulse_relay(duration_s: float = 0.5) -> None:
    """
    Pulse GPIO22 HIGH for `duration_s` seconds, then release cleanly.

    Short pulse (~0.5s) = momentary press → power ON
    Long  pulse (~5.5s) = held press      → force OFF

    Blocks for duration_s + ~0.2s settle time.
    """
    with _relay_lock:
        logger.debug(f"Relay: HIGH for {duration_s}s")

        # Phase 1: OUTPUT LOW → HIGH → release (relay clicks ON, then latches)
        with gpiod.request_lines(
            PinConfig.CHIP,
            consumer=PinConfig.CONSUMER,
            config={PinConfig.RELAY: gpiod.LineSettings(
                direction=Direction.OUTPUT,
                output_value=Value.INACTIVE     # start LOW before going HIGH
            )}
        ) as req:
            req.set_value(PinConfig.RELAY, Value.INACTIVE)
            time.sleep(0.05)                    # brief LOW before pulse
            req.set_value(PinConfig.RELAY, Value.ACTIVE)
            time.sleep(duration_s)
        # OUTPUT released — line floats HIGH, relay stays latched momentarily

        # Phase 2: INPUT pulls line LOW → relay releases, no latch on INPUT release
        with gpiod.request_lines(
            PinConfig.CHIP,
            consumer=PinConfig.CONSUMER,
            config={PinConfig.RELAY: gpiod.LineSettings(
                direction=Direction.INPUT
            )}
        ) as req:
            time.sleep(0.2)                     # hold as INPUT to settle LOW
        # INPUT released — line floats LOW, relay stays OFF

        logger.debug("Relay: released cleanly")


def pulse_relay_async(duration_s: float = 0.5) -> threading.Thread:
    """
    Same as pulse_relay() but non-blocking — runs in a background thread.
    Returns the Thread so caller can .join() if needed.
    """
    t = threading.Thread(target=pulse_relay, args=(duration_s,), daemon=True)
    t.start()
    return t
