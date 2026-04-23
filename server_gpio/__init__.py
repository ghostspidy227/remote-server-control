"""
server_gpio — Raspberry Pi 5 GPIO control for server power management.

Quick reference
---------------
from server_gpio import power, monitor, pins

# Read state
monitor.current_state(host="192.168.1.10")   # → ServerState.ON / OFF / HUNG

# Power control
power.power_on(host="192.168.1.10")
power.power_off(force=False)
power.reboot(host="192.168.1.10")

# Watch for changes (blocking generator)
for old, new in monitor.watch(host="192.168.1.10"):
    print(f"{old} → {new}")

# Raw pin access
pins.is_server_on()
pins.is_ssd_active()
pins.pulse_relay(0.5)
"""

from . import pins, monitor, power
from .monitor import ServerState

__all__ = ["pins", "monitor", "power", "ServerState"]
