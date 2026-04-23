"""
ws_agent.py — Example WebSocket agent that exposes server power control.

Accepts JSON commands over a WebSocket connection.

Commands (send as JSON):
  {"action": "status"}
  {"action": "power_on"}
  {"action": "power_off"}
  {"action": "power_off", "force": true}
  {"action": "reboot"}
  {"action": "reboot", "force_off": true}

Responses are JSON:
  {"ok": true,  "state": "ON", "result": "..."}
  {"ok": false, "error": "..."}

Install deps:
  pip install websockets

Run:
  python ws_agent.py --host 0.0.0.0 --port 8765 --server-host 192.168.1.10
"""

import asyncio
import json
import logging
import argparse
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import websockets
from server_gpio import power, monitor, ServerState

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("ws_agent")


# ── Handler ────────────────────────────────────────────────────────────────────

async def handle(websocket, server_host: str):
    client = websocket.remote_address
    logger.info(f"Client connected: {client}")

    async for message in websocket:
        try:
            cmd = json.loads(message)
        except json.JSONDecodeError:
            await websocket.send(json.dumps({"ok": False, "error": "Invalid JSON"}))
            continue

        action = cmd.get("action", "")
        logger.info(f"[{client}] action={action}")

        try:
            response = await dispatch(action, cmd, server_host)
        except Exception as e:
            logger.exception(f"Error handling action={action}")
            response = {"ok": False, "error": str(e)}

        await websocket.send(json.dumps(response))

    logger.info(f"Client disconnected: {client}")


async def dispatch(action: str, cmd: dict, server_host: str) -> dict:
    loop = asyncio.get_event_loop()

    if action == "status":
        state = await loop.run_in_executor(
            None, lambda: monitor.current_state(host=server_host)
        )
        ssd = await loop.run_in_executor(None, lambda: __import__("server_gpio.pins", fromlist=["is_ssd_active"]).is_ssd_active())
        return {"ok": True, "state": state.name, "ssd_active": ssd}

    elif action == "power_on":
        result = await loop.run_in_executor(
            None, lambda: power.power_on(wait_for_boot=True, host=server_host)
        )
        return {"ok": result, "result": "Server is on" if result else "Boot timeout"}

    elif action == "power_off":
        force = cmd.get("force", False)
        result = await loop.run_in_executor(
            None, lambda: power.power_off(force=force, wait_for_off=True)
        )
        return {"ok": result, "result": "Server is off" if result else "Shutdown timeout"}

    elif action == "reboot":
        force_off = cmd.get("force_off", False)
        result = await loop.run_in_executor(
            None, lambda: power.reboot(host=server_host, force_off=force_off)
        )
        return {"ok": result, "result": "Reboot complete" if result else "Reboot failed"}

    else:
        return {"ok": False, "error": f"Unknown action: {action!r}"}


# ── State change broadcaster ───────────────────────────────────────────────────

CONNECTED_CLIENTS: set = set()

async def state_broadcaster(server_host: str):
    """Pushes state-change events to all connected clients."""
    loop = asyncio.get_event_loop()
    last_state = ServerState.UNKNOWN

    while True:
        state = await loop.run_in_executor(
            None, lambda: monitor.current_state(host=server_host)
        )
        if state != last_state:
            event = json.dumps({"event": "state_change", "old": last_state.name, "new": state.name})
            for ws in list(CONNECTED_CLIENTS):
                try:
                    await ws.send(event)
                except Exception:
                    pass
            last_state = state
        await asyncio.sleep(5)


# ── Main ───────────────────────────────────────────────────────────────────────

async def main(host: str, port: int, server_host: str):
    async def handler(ws):
        CONNECTED_CLIENTS.add(ws)
        try:
            await handle(ws, server_host)
        finally:
            CONNECTED_CLIENTS.discard(ws)

    asyncio.create_task(state_broadcaster(server_host))

    logger.info(f"WebSocket agent listening on ws://{host}:{port}")
    logger.info(f"Monitoring server at {server_host}")
    async with websockets.serve(handler, host, port):
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host",        default="0.0.0.0")
    parser.add_argument("--port",        type=int, default=8765)
    parser.add_argument("--server-host", default="192.168.1.10",
                        help="IP/hostname of the server to monitor and control")
    args = parser.parse_args()

    asyncio.run(main(args.host, args.port, args.server_host))
