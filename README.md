# remote-server-control

Keep your server off. Turn it on when you need it — from anywhere.

A Raspberry Pi 5 sits between you and your main server. It monitors the server's power state via GPIO, controls the power button via a relay, and exposes a token-based HTTP API for other services on the network. You control everything through a Telegram bot.

The server stays off by default. Any service that needs it — a backup job, a local LLM, a database sync — calls the API to acquire a token, which powers the server on. When all tokens are released, the server shuts back down. If you need manual control, the Telegram bot has you covered from anywhere outside your network.

---

## Install

Clone the repo onto your Pi 5:

```bash
git clone https://github.com/ghostspidy227/remote-server-control.git
cd remote-server-control
```

Run the install script as root:

```bash
sudo bash install.sh
```

The script will walk you through the setup interactively — it asks for your Telegram bot token, your Telegram user ID, and the IP address of the server you want to control. Press `H` at any prompt for inline help on how to get that value.

When it's done, start the services:

```bash
sudo systemctl start server_ctrl tg_bot
```

Check they're running:

```bash
sudo systemctl status server_ctrl tg_bot
```

Open Telegram and send `/start` to your bot.

### Re-running the install script

Running `sudo bash install.sh` again on an already-installed system opens an update menu instead of re-doing the full install. From there you can change your bot token, update the server IP, add or remove a Telegram user ID, reinstall dependencies, or repair broken services — without touching anything that's already working.

To fully remove the installation:

```bash
sudo bash uninstall.sh
```

---

## Requirements

- Raspberry Pi 5 (tested on Debian Trixie, kernel 6.12, Python 3.13)
- Python 3.11 or newer
- A Telegram bot token ([`@BotFather`](https://t.me/BotFather))
- Your Telegram user ID ([`@userinfobot`](https://t.me/userinfobot))

Python dependencies are installed automatically into a virtualenv under the install directory — nothing is installed system-wide.

---

## How it works

### Token system

`server_ctrl.py` runs as a daemon on `localhost:7070`. It is the only process that touches GPIO directly — nothing else calls the pins.

Any service that needs the server sends a `POST /on` with its name. The daemon issues it a **token** (a short-lived lease) and fires the relay to power the server on if it isn't already. The server stays on as long as at least one token is active. When a service is done, it sends `POST /off` with its token. If that was the last token, the daemon fires the relay to shut the server down.

Each token has a heartbeat — services must send `POST /renew` every 30 seconds or the token is marked expired. Expired tokens trigger a Telegram notification so you know something stopped renewing, but they do **not** automatically power the server off. That decision stays with you.

### GPIO watcher

A background thread polls GPIO17 every 5 seconds. If the server goes off without the daemon initiating it — physical button press, crash, whatever — all active tokens are force-cleared and you get a Telegram notification.

### Telegram bot

The bot gives you direct manual control:

|Command|What it does|
|---|---|
|`/status`|Server state (ON / OFF / BOOTING / HUNG) + ping|
|`/ping`|Quick ICMP ping to the server|
|`/on`|Short relay pulse — powers the server on|
|`/off`|Short relay pulse — soft power off (initiates OS shutdown)|
|`/force`|4-second relay hold — sends a long power button press|
|`/reboot`|Soft off followed by power on|

> **Note on `/force`:** Due to inverted relay logic, the force command sends a long power button signal to the motherboard. Depending on your BIOS settings this may still be a graceful shutdown rather than a hard cut.

Only Telegram user IDs listed in `notify.py` can issue commands. All power events send you a push notification automatically.

---

## API reference

`server_ctrl` runs on `http://localhost:7070`. Services on the same network reach it at `http://<pi-ip>:7070`.

```
POST /on       {"service": "my-service"}
               → issues a token, powers server on if needed
               ← {"token": "abc123", "action": "power_on" | "already_on"}

POST /off      {"token": "abc123", "service": "my-service"}
               → releases token, powers server off if last token
               ← {"status": "ok", "action": "power_off" | "none"}

POST /renew    {"token": "abc123"}
               → heartbeat, resets expiry timer
               ← {"status": "ok"}

GET  /status   → current server state + all active tokens
GET  /ping     → ICMP ping result
GET  /health   → daemon alive check
```

Tokens expire after 90 seconds without a renewal (30s interval × 3 grace).

---

## Hardware

![Hardware photo](https://claude.ai/chat/pic.jpeg)

**Components:**

- 2-channel relay module (SRD-05VDC-SL-C) — one relay used, wired to the motherboard's ATX power switch header
- 4N25 optocouplers × 2 — isolate the server's power LED and SSD LED signals from the Pi's GPIO inputs
- Raspberry Pi 5

**Pin assignments:**

|GPIO|Role|Notes|
|---|---|---|
|GPIO17|Power LED input|INACTIVE (low) = server ON. Logic is inverted — LED pulls line low when lit|
|GPIO27|SSD LED input|INACTIVE (low) = SSD busy. Same inverted logic|
|GPIO22|Relay output|ACTIVE (high) = relay closes = power button pressed|

---

## Project structure

```
remote-server-control/
├── server_ctrl.py        # Power control daemon — HTTP API + GPIO owner
├── tg_bot.py             # Telegram bot — manual control interface
├── notify.py             # Shared Telegram push notification module (config lives here)
├── gpiosim.py            # Live GPIO monitor for debugging pin states
├── test_pins.py          # Interactive hardware test suite
├── install.sh            # Install / update script
├── uninstall.sh          # Clean removal script
└── server_gpio/
    ├── pins.py           # Low-level GPIO read/write (gpiod v2)
    ├── monitor.py        # Server state detection (LED + ping)
    ├── power.py          # Power sequences (on, off, reboot)
    ├── watchdog.py       # Unexpected shutdown detection
    └── ws_agent.py       # WebSocket agent for external integrations
```

---

## Configuration

All config lives in `notify.py` in the install directory (default `/opt/power_control`):

```python
BOT_TOKEN     = "your-token"
ALLOWED_USERS = {123456789}
SERVER_IP     = "192.168.1.x"
```

The install script writes these values for you. To change them later, re-run `sudo bash install.sh` and use the update menu.

---

## Debugging

Live GPIO monitor — shows real-time pin states, useful for verifying wiring:

```bash
cd /opt/power_control
venv/bin/python3 gpiosim.py              # poll every 1s
venv/bin/python3 gpiosim.py --poll 0.2  # faster (catch SSD blinks)
venv/bin/python3 gpiosim.py --once      # single snapshot
venv/bin/python3 gpiosim.py --events    # edge-triggered, instant
```

Hardware test suite — interactive step-by-step test of all three pins:

```bash
venv/bin/python3 test_pins.py
```

Service logs:

```bash
journalctl -u server_ctrl -f
journalctl -u tg_bot -f
```

---

## License

MIT — see [LICENSE](https://claude.ai/chat/LICENSE).