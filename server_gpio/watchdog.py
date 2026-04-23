"""
watchdog.py — Standalone watchdog daemon.

Monitors the server and force-reboots it if it's detected as hung
(power LED on + ping failing).

Usage:
  python watchdog.py --server-host 192.168.1.10 [--poll 30] [--dry-run]
"""

import time
import logging
import argparse
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from server_gpio import power, monitor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("watchdog")


def run(server_host: str, poll_s: float, dry_run: bool):
    logger.info(f"Watchdog started | host={server_host} poll={poll_s}s dry_run={dry_run}")

    for old, new in monitor.watch(host=server_host, poll_interval_s=poll_s):
        logger.info(f"State change: {old.name} → {new.name}")

        if new == monitor.ServerState.HUNG:
            logger.warning("Server is HUNG — power LED on but unreachable")
            if dry_run:
                logger.info("[DRY RUN] Would force-reboot now")
            else:
                logger.info("Initiating force reboot...")
                ok = power.reboot(host=server_host, force_off=True)
                if ok:
                    logger.info("Reboot successful ✓")
                else:
                    logger.error("Reboot failed — manual intervention needed")

        elif new == monitor.ServerState.ON:
            logger.info("Server is UP ✓")

        elif new == monitor.ServerState.OFF:
            logger.info("Server is OFF")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-host", required=True)
    parser.add_argument("--poll",    type=float, default=30.0, help="Poll interval seconds")
    parser.add_argument("--dry-run", action="store_true", help="Detect but don't reboot")
    args = parser.parse_args()

    run(args.server_host, args.poll, args.dry_run)
