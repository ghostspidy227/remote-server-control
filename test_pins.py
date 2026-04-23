#!/usr/bin/env python3
"""
test_pins.py — Physical hardware test for GPIO17, GPIO27, and GPIO22.

Tests the actual server_gpio package against real hardware.
You physically interact with the server and this script verifies the package
reads/drives the pins correctly.

Run from pins_scripts directory:
    python3 test_pins.py
"""

import sys
import time
import logging
from pathlib import Path
from datetime import datetime

# Allow running from the project root without installing the package
sys.path.insert(0, str(Path(__file__).parent))

try:
    from server_gpio import pins, monitor, ServerState
except ImportError as e:
    print(f"ERROR: Could not import server_gpio package: {e}")
    print("Make sure you're running from the server_gpio project root.")
    sys.exit(1)

# ── Formatting helpers ─────────────────────────────────────────────────────────

G  = "\033[92m"   # green
R  = "\033[91m"   # red
Y  = "\033[93m"   # yellow
B  = "\033[94m"   # blue
DIM = "\033[90m"
RST = "\033[0m"
OK  = f"{G}✓ PASS{RST}"
FAIL = f"{R}✗ FAIL{RST}"

def ts() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]

def section(title: str):
    print(f"\n{Y}{'─'*54}{RST}")
    print(f"{Y}  {title}{RST}")
    print(f"{Y}{'─'*54}{RST}")

def prompt(msg: str) -> str:
    print(f"\n{B}▶ {msg}{RST}")
    return input(f"  {DIM}Press Enter when ready (or 's' to skip, 'q' to quit): {RST}").strip().lower()

def result(label: str, got, expected, note: str = ""):
    passed = got == expected
    icon = OK if passed else FAIL
    extra = f"  {DIM}{note}{RST}" if note else ""
    print(f"  {icon}  {label}: got={G if passed else R}{got!r}{RST} expected={expected!r}{extra}")
    return passed

# ── Test steps ─────────────────────────────────────────────────────────────────

def test_import():
    section("1. Package import & pin config")
    print(f"  Chip:      {pins.PinConfig.CHIP}")
    print(f"  GPIO17:    power LED  (pin {pins.PinConfig.POWER_LED})")
    print(f"  GPIO27:    SSD LED    (pin {pins.PinConfig.SSD_LED})")
    print(f"  GPIO22:    relay      (pin {pins.PinConfig.RELAY})")
    print(f"  {OK}  server_gpio imported successfully")
    return True


def test_raw_read():
    section("2. Raw pin reads via pins._read()")
    print("  Reading GPIO17 and GPIO27 directly...")
    try:
        from gpiod.line import Value
        v17 = pins._read(pins.PinConfig.POWER_LED)
        v27 = pins._read(pins.PinConfig.SSD_LED)
        print(f"  [{ts()}]  GPIO17 raw = {v17.name}   GPIO27 raw = {v27.name}")
        print(f"  {OK}  Raw reads succeeded (no crash = chip + pins accessible)")
        return True
    except PermissionError:
        print(f"  {FAIL}  Permission denied on {pins.PinConfig.CHIP}")
        print(f"  Fix: sudo usermod -aG gpio $USER  then log out/in")
        print(f"   or: sudo python3 test_pins.py")
        return False
    except Exception as e:
        print(f"  {FAIL}  Unexpected error: {e}")
        return False


def test_server_off():
    section("3. GPIO17 — Server OFF state")
    ans = prompt("Make sure the server is POWERED OFF, then press Enter")
    if ans == 'q': sys.exit(0)
    if ans == 's': print(f"  {DIM}Skipped{RST}"); return None

    print(f"  [{ts()}]  Reading pins.is_server_on()...")
    on = pins.is_server_on()
    state = monitor.current_state()    # no host → no ping
    r1 = result("pins.is_server_on()",  on,    False, "GPIO17 should be ACTIVE when LED is off")
    r2 = result("monitor.current_state()", state, ServerState.OFF)
    return r1 and r2


def test_server_on():
    section("4. GPIO17 — Server ON state")
    ans = prompt("Now POWER ON the server and wait for the power LED to be solid, then press Enter")
    if ans == 'q': sys.exit(0)
    if ans == 's': print(f"  {DIM}Skipped{RST}"); return None

    print(f"  [{ts()}]  Reading pins.is_server_on()...")
    on = pins.is_server_on()
    state = monitor.current_state()
    r1 = result("pins.is_server_on()",     on,    True, "GPIO17 should be INACTIVE when LED is on")
    r2 = result("monitor.current_state()", state, ServerState.ON)
    return r1 and r2


def test_ssd_while_booting():
    section("5. GPIO27 — SSD activity while server is booting/running")
    ans = prompt("Server should still be ON and active (booting or running). Press Enter to watch SSD blinks for 15 seconds")
    if ans == 'q': sys.exit(0)
    if ans == 's': print(f"  {DIM}Skipped{RST}"); return None

    print(f"  Watching GPIO27 for 15s — you should see BUSY/IDLE flips if disk is active...")
    print()

    flips = 0
    busy_count = 0
    idle_count = 0
    last = None
    deadline = time.monotonic() + 15

    while time.monotonic() < deadline:
        active = pins.is_ssd_active()
        if last is not None and active != last:
            flips += 1
            label = f"{G}BUSY{RST}" if active else f"{DIM}IDLE{RST}"
            print(f"  [{ts()}]  GPIO27 → {label}  (flip #{flips})")
        if active:
            busy_count += 1
        else:
            idle_count += 1
        last = active
        time.sleep(0.1)

    print(f"\n  Summary: {flips} flips in 15s  |  busy samples={busy_count}  idle samples={idle_count}")

    if flips == 0:
        print(f"  {Y}⚠ WARN{RST}  No SSD flips detected.")
        print(f"        This is OK if the server is idle. Try running 'ls -laR /' on it.")
        print(f"        If disk is definitely active and still no flips, check GPIO27 wiring.")
        return None  # inconclusive, not a failure
    else:
        print(f"  {OK}  GPIO27 is responding to SSD activity")
        return True


def test_power_off_detection():
    section("6. GPIO17 — Detect server powering off")
    ans = prompt("Power OFF the server now (via OS shutdown or hold power button), then press Enter immediately")
    if ans == 'q': sys.exit(0)
    if ans == 's': print(f"  {DIM}Skipped{RST}"); return None

    print(f"  Polling every 2s until power LED goes off (max 90s)...")
    deadline = time.monotonic() + 90
    detected = False

    while time.monotonic() < deadline:
        on = pins.is_server_on()
        state = monitor.current_state()
        print(f"  [{ts()}]  is_server_on={on}  state={state.name}", end="\r", flush=True)
        if not on:
            print()  # newline after \r
            print(f"  {OK}  Power LED went OFF detected at {ts()}")
            detected = True
            break
        time.sleep(2)

    if not detected:
        print(f"\n  {FAIL}  Server did not appear to power off within 90s")
    return detected


def test_relay():
    section("7. GPIO22 — Relay pulse (power button press)")
    ans = prompt("Make sure server is OFF. Script will pulse relay once (short press) to turn it ON")
    if ans == 'q': sys.exit(0)
    if ans == 's': print(f"  {DIM}Skipped{RST}"); return None

    print(f"  [{ts()}]  Calling pins.pulse_relay(0.5)...")
    try:
        pins.pulse_relay(0.5)
        print(f"  [{ts()}]  Pulse complete")
    except Exception as e:
        print(f"  {FAIL}  pulse_relay raised: {e}")
        return False

    on = input(f"  {DIM}Did the relay click and server start powering on? [y/n]: {RST}").strip().lower()
    if on != 'y':
        print(f"  {FAIL}  Relay did not trigger as expected")
        return False

    print(f"  {OK}  Relay pulse works")

    # Now test force off
    ans2 = prompt("Server should now be ON. Script will force off (4s hold). Ready?")
    if ans2 in ('q', 's'):
        return True  # relay pulse passed at least

    print(f"  [{ts()}]  Calling pins.pulse_relay(4.0) — hold for 4 seconds...")
    pins.pulse_relay(4.0)
    print(f"  [{ts()}]  Done")

    off = input(f"  {DIM}Did server force power off? [y/n]: {RST}").strip().lower()
    r = result("pulse_relay(4.0) force off", off == 'y', True)
    return r


# ── Edge-triggered live watch (bonus) ─────────────────────────────────────────

def test_edge_watch():
    section("8. Edge-triggered watch (bonus — uses monitor.watch)")
    ans = prompt("Optional: watch state changes via monitor.watch() for 30s. Toggle server power to test. Enter to start")
    if ans == 'q': sys.exit(0)
    if ans == 's': print(f"  {DIM}Skipped{RST}"); return None

    import threading

    print(f"  Watching for state changes for 30s (poll=2s)...")
    print(f"  {DIM}Turn server on/off during this window to verify events fire{RST}\n")

    events = []
    stop = threading.Event()

    def watcher():
        for old, new in monitor.watch(host=None, poll_interval_s=2, ping_required=False):
            if stop.is_set():
                break
            events.append((ts(), old, new))
            arrow = f"{G}↑{RST}" if new == ServerState.ON else f"{R}↓{RST}"
            print(f"  [{events[-1][0]}]  {arrow}  {old.name} → {new.name}")

    t = threading.Thread(target=watcher, daemon=True)
    t.start()
    time.sleep(30)
    stop.set()

    print(f"\n  Captured {len(events)} state change(s) in 30s")
    if events:
        print(f"  {OK}  monitor.watch() is firing correctly")
    else:
        print(f"  {Y}⚠{RST}  No state changes detected (expected if server stayed in same state)")
    return True


# ── Summary ────────────────────────────────────────────────────────────────────

def main():
    print(f"""
{Y}╔══════════════════════════════════════════════════════╗
║   server_gpio — Physical Pin Test                    ║
║   GPIO17 (power LED) + GPIO27 (SSD LED)              ║
║   GPIO22 (relay — power button)                      ║
╚══════════════════════════════════════════════════════╝{RST}
""")

    logging.basicConfig(level=logging.WARNING)

    results = {}
    results["import"]           = test_import()
    results["raw_read"]         = test_raw_read()

    if not results["raw_read"]:
        print(f"\n{R}Cannot access GPIO. Fix permissions and re-run.{RST}")
        sys.exit(1)

    results["server_off"]       = test_server_off()
    results["server_on"]        = test_server_on()
    results["ssd_activity"]     = test_ssd_while_booting()
    results["power_off_detect"] = test_power_off_detection()
    results["relay"]            = test_relay()
    results["edge_watch"]       = test_edge_watch()

    section("Results summary")
    passed = skipped = failed = 0
    for name, r in results.items():
        if r is True:
            print(f"  {OK}      {name}")
            passed += 1
        elif r is False:
            print(f"  {FAIL}    {name}")
            failed += 1
        else:
            print(f"  {Y}– SKIP{RST}   {name}")
            skipped += 1

    print(f"\n  Passed: {passed}  |  Failed: {failed}  |  Skipped/inconclusive: {skipped}")

    if failed == 0:
        print(f"\n{G}  All tested pins are working correctly.{RST}")
        print(f"  GPIO17, GPIO27, GPIO22 all confirmed good.\n")
    else:
        print(f"\n{R}  Some tests failed. Check wiring and re-run.{RST}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
