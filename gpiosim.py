#!/usr/bin/env python3
"""
gpio_sim.py — Live monitor for GPIO17 (power LED), GPIO27 (SSD LED), GPIO22 (relay).

Uses the gpiod Python library v2.x directly — no subprocess, no gpioget.

Confirmed for:
  Debian Trixie (13.4) | kernel 6.12 | Python 3.13 | gpiod 2.2.0
  gpiochip0 [pinctrl-rp1] — the main GPIO chip on Pi 5

Pin logic (inverted — LEDs pull lines LOW when lit):
  GPIO17: INACTIVE = LED on = server ON  |  ACTIVE = LED off = server OFF
  GPIO27: INACTIVE = LED on = SSD busy   |  ACTIVE = LED off = SSD idle
  GPIO22: ACTIVE   = relay pressed       |  INACTIVE = relay released

Usage:
  python3 gpio_sim.py                # live monitor, 1s poll
  python3 gpio_sim.py --poll 0.3     # faster poll (good for catching SSD blinks)
  python3 gpio_sim.py --once         # single snapshot and exit
  python3 gpio_sim.py --events       # edge-triggered mode (instant, no polling)
"""

import sys
import time
import argparse
from datetime import datetime

try:
    import gpiod
    from gpiod.line import Direction, Value, Edge
except ImportError:
    print("ERROR: gpiod not found. Run:  pip3 install gpiod")
    sys.exit(1)

# ── Config ─────────────────────────────────────────────────────────────────────

CHIP        = "/dev/gpiochip0"   # pinctrl-rp1 on Pi 5 (confirmed via gpiodetect)
PIN_POWER   = 17
PIN_SSD     = 27
PIN_RELAY   = 22
CONSUMER    = "gpio_sim"


# ── Logic helpers ──────────────────────────────────────────────────────────────

def server_on(v: Value) -> bool:
    """GPIO17 INACTIVE (low) = power LED lit = server is ON."""
    return v == Value.INACTIVE

def ssd_busy(v: Value) -> bool:
    """GPIO27 INACTIVE (low) = SSD LED lit = SSD is busy."""
    return v == Value.INACTIVE

def relay_pressed(v: Value) -> bool:
    """GPIO22 ACTIVE (high) = relay is pressing the power button."""
    return v == Value.ACTIVE

def fmt_server(v: Value) -> str:
    on = server_on(v)
    color = "\033[92m" if on else "\033[91m"
    label = "ON " if on else "OFF"
    return f"{color}{label}\033[0m"

def fmt_ssd(v: Value) -> str:
    busy = ssd_busy(v)
    color = "\033[94m" if busy else "\033[90m"
    label = "BUSY" if busy else "IDLE"
    return f"{color}{label}\033[0m"

def fmt_relay(v: Value) -> str:
    pressed = relay_pressed(v)
    color = "\033[93m" if pressed else "\033[90m"
    label = "PRESSED" if pressed else "IDLE   "
    return f"{color}{label}\033[0m"

def raw_str(v: Value) -> str:
    return "inactive" if v == Value.INACTIVE else "active  "


# ── Polling mode ───────────────────────────────────────────────────────────────

def run_poll(poll_s: float):
    """Poll all pins every poll_s seconds and print a live single-line display."""
    settings = gpiod.LineSettings(direction=Direction.INPUT)

    with gpiod.request_lines(
        CHIP,
        consumer=CONSUMER,
        config={
            PIN_POWER: settings,
            PIN_SSD:   settings,
            PIN_RELAY: settings,
        }
    ) as req:
        print("Polling mode — Ctrl+C to stop\n")
        ssd_flips = 0
        last_ssd  = None

        while True:
            pwr   = req.get_value(PIN_POWER)
            ssd   = req.get_value(PIN_SSD)
            relay = req.get_value(PIN_RELAY)
            ts    = datetime.now().strftime("%H:%M:%S.%f")[:-3]

            if last_ssd is not None and ssd != last_ssd:
                ssd_flips += 1
            last_ssd = ssd

            print(
                f"\r[{ts}]  "
                f"GPIO17 {raw_str(pwr)} → Server: {fmt_server(pwr)}   "
                f"GPIO27 {raw_str(ssd)} → SSD: {fmt_ssd(ssd)}  "
                f"GPIO22 {raw_str(relay)} → Relay: {fmt_relay(relay)}  "
                f"[SSD flips: {ssd_flips:4d}]",
                end="", flush=True
            )
            time.sleep(poll_s)


# ── Edge-triggered mode ────────────────────────────────────────────────────────

def run_events():
    """
    Block on kernel edge events — fires instantly on any pin change.
    More responsive than polling; ideal for catching fast SSD blinks.
    """
    with gpiod.request_lines(
        CHIP,
        consumer=CONSUMER,
        config={
            PIN_POWER: gpiod.LineSettings(edge_detection=Edge.BOTH),
            PIN_SSD:   gpiod.LineSettings(edge_detection=Edge.BOTH),
            PIN_RELAY: gpiod.LineSettings(edge_detection=Edge.BOTH),
        }
    ) as req:
        print("Edge-triggered mode — fires on any pin change. Ctrl+C to stop\n")

        pwr   = req.get_value(PIN_POWER)
        ssd   = req.get_value(PIN_SSD)
        relay = req.get_value(PIN_RELAY)
        print(f"Initial:  GPIO17={raw_str(pwr)} (Server {fmt_server(pwr)})  "
              f"GPIO27={raw_str(ssd)} (SSD {fmt_ssd(ssd)})  "
              f"GPIO22={raw_str(relay)} (Relay {fmt_relay(relay)})\n")

        pin_names = {PIN_POWER: "GPIO17(power)", PIN_SSD: "GPIO27(ssd  )", PIN_RELAY: "GPIO22(relay)"}
        state     = {PIN_POWER: pwr, PIN_SSD: ssd, PIN_RELAY: relay}

        while True:
            for event in req.read_edge_events():
                pin   = event.line_offset
                etype = "↑ RISING " if event.event_type == event.Type.RISING_EDGE else "↓ FALLING"
                ts    = datetime.now().strftime("%H:%M:%S.%f")[:-3]

                state[pin] = (Value.ACTIVE
                              if event.event_type == event.Type.RISING_EDGE
                              else Value.INACTIVE)

                print(
                    f"[{ts}]  {pin_names[pin]}  {etype}  "
                    f"→  Server: {fmt_server(state[PIN_POWER])}  "
                    f"SSD: {fmt_ssd(state[PIN_SSD])}  "
                    f"Relay: {fmt_relay(state[PIN_RELAY])}"
                )


# ── Single snapshot ────────────────────────────────────────────────────────────

def run_once():
    settings = gpiod.LineSettings(direction=Direction.INPUT)
    with gpiod.request_lines(
        CHIP,
        consumer=CONSUMER,
        config={PIN_POWER: settings, PIN_SSD: settings, PIN_RELAY: settings}
    ) as req:
        pwr   = req.get_value(PIN_POWER)
        ssd   = req.get_value(PIN_SSD)
        relay = req.get_value(PIN_RELAY)

    print(f"""
Snapshot @ {datetime.now().strftime("%H:%M:%S")}
  GPIO17 (power LED):  {raw_str(pwr)}   →  Server is {'ON' if server_on(pwr) else 'OFF'}
  GPIO27 (SSD LED):    {raw_str(ssd)}   →  SSD is {'BUSY' if ssd_busy(ssd) else 'IDLE'}
  GPIO22 (relay):      {raw_str(relay)} →  Relay is {'PRESSED' if relay_pressed(relay) else 'IDLE'}
""")


# ── Main ───────────────────────────────────────────────────────────────────────

BANNER = """\
┌──────────────────────────────────────────────────────┐
│  GPIO Monitor — Pi 5  |  gpiochip0 [pinctrl-rp1]    │
│  GPIO17 → Power LED   |  GPIO27 → SSD LED            │
│  GPIO22 → Relay (power button)                       │
└──────────────────────────────────────────────────────┘"""

def main():
    parser = argparse.ArgumentParser(description="GPIO17/27 live monitor (gpiod v2)")
    parser.add_argument("--poll",   type=float, default=1.0,
                        help="Poll interval seconds (default 1.0). Use 0.2 to catch SSD blinks.")
    parser.add_argument("--once",   action="store_true", help="Single snapshot then exit")
    parser.add_argument("--events", action="store_true", help="Edge-triggered mode (instant, no polling)")
    args = parser.parse_args()

    print(BANNER + "\n")

    try:
        if args.once:
            run_once()
        elif args.events:
            run_events()
        else:
            run_poll(args.poll)
    except KeyboardInterrupt:
        print("\n\nStopped.")
    except PermissionError:
        print(f"\nERROR: Permission denied on {CHIP}")
        print("Fix:  sudo usermod -aG gpio $USER  (then log out and back in)")
        print("  or: sudo python3 gpio_sim.py")
        sys.exit(1)


if __name__ == "__main__":
    main()
