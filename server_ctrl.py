"""
server_ctrl.py — Central server power control daemon.

Single process that owns all GPIO access for power control.
All other services talk to this via HTTP — nothing calls pins.py directly.

Runs on: http://localhost:7070

Endpoints:
  POST /on       {"service": "name"}                  → acquire token, power on if needed
  POST /off      {"token": "...", "service": "name"}  → release token, power off if last
  POST /renew    {"token": "..."}                      → heartbeat, reset expiry timer
  GET  /status                                         → server state + all active tokens
  GET  /ping                                           → ICMP ping to server
  GET  /health                                         → daemon alive check

Token system:
  - Every service needing the server ON holds a token (lease)
  - Server stays ON as long as any token is active
  - Each token must be renewed every HEARTBEAT_INTERVAL seconds
  - After HEARTBEAT_GRACE missed renewals, token expires → Telegram notification
  - Server powers OFF only when last token is cleanly released via POST /off
  - Expired tokens do NOT trigger power off — humans decide via Telegram

Race condition protection:
  - _shutdown_lock is held atomically during "last token → fire relay" and "new token → fire relay"
  - Prevents a new /on from racing with the final /off relay pulse

GPIO17 watcher:
  - Background thread polls is_server_on() every WATCHER_POLL_S seconds
  - If server goes OFF without the daemon initiating it (Telegram /force, physical button, crash)
  - All active tokens are force-cleared and Telegram is notified

Run:
  pip install fastapi uvicorn httpx --break-system-packages
  sudo python3 server_ctrl.py

Systemd service: see server_ctrl.service
"""

import os
import sys
sys.path.insert(0, "/dir")

import uuid
import time
import threading
import logging
import subprocess
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

from server_gpio import pins
from notify import notify, SERVER_IP

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────

PORT                = 7070
HEARTBEAT_INTERVAL  = 30     # seconds — service must renew within this
HEARTBEAT_GRACE     = 2      # missed beats before expiry
EXPIRY_SECONDS      = HEARTBEAT_INTERVAL * (1 + HEARTBEAT_GRACE)   # 90s
WATCHER_POLL_S      = 5      # how often GPIO17 watcher polls
WATCHER_DEBOUNCE    = 2      # consecutive OFF reads before declaring unexpected shutdown

# ── Token store ────────────────────────────────────────────────────────────────

@dataclass
class Token:
    token:      str
    service:    str
    issued_at:  float = field(default_factory=time.monotonic)
    last_seen:  float = field(default_factory=time.monotonic)

    def is_expired(self) -> bool:
        return (time.monotonic() - self.last_seen) > EXPIRY_SECONDS

    def renew(self):
        self.last_seen = time.monotonic()

    def age_str(self) -> str:
        secs = int(time.monotonic() - self.last_seen)
        return f"{secs}s ago"

    def to_dict(self) -> dict:
        return {
            "token":      self.token,
            "service":    self.service,
            "issued_at":  datetime.fromtimestamp(
                              time.time() - (time.monotonic() - self.issued_at)
                          ).strftime("%H:%M:%S"),
            "last_seen":  self.age_str(),
        }


# ── State ──────────────────────────────────────────────────────────────────────

_tokens:        dict[str, Token] = {}   # token_str → Token
_shutdown_lock  = threading.Lock()      # held during token-count → relay-fire decision
_tokens_lock    = threading.Lock()      # protects _tokens dict mutations

# ── Token helpers ──────────────────────────────────────────────────────────────

def _new_token(service: str) -> Token:
    t = Token(token=uuid.uuid4().hex[:12], service=service)
    with _tokens_lock:
        _tokens[t.token] = t
    logger.info(f"Token issued: {t.token} → {service}  (total: {len(_tokens)})")
    return t


def _remove_token(token_str: str) -> Optional[Token]:
    with _tokens_lock:
        return _tokens.pop(token_str, None)


def _token_count() -> int:
    with _tokens_lock:
        return len(_tokens)


def _all_tokens() -> list[Token]:
    with _tokens_lock:
        return list(_tokens.values())


def _force_clear_all() -> list[str]:
    """Clear all tokens. Returns list of service names that were cleared."""
    with _tokens_lock:
        services = [t.service for t in _tokens.values()]
        _tokens.clear()
    return services

# ── Core power logic ───────────────────────────────────────────────────────────

def _do_power_on():
    """Fire relay to power on. Call only while holding _shutdown_lock."""
    logger.info("Firing relay → power ON")
    pins.pulse_relay_async(0.5)


def _do_power_off():
    """Fire relay to power off. Call only while holding _shutdown_lock."""
    logger.info("Firing relay → power OFF")
    pins.pulse_relay_async(0.5)


def acquire_token(service: str) -> tuple[Token, bool]:
    """
    Issue a token to a service. Power on the server if it was off.
    Returns (token, fired) where fired=True means relay was pulsed.

    Holds _shutdown_lock during the decision so a concurrent /off
    can't slip a shutdown between our token-add and relay-fire.
    """
    with _shutdown_lock:
        token = _new_token(service)
        server_was_off = not pins.is_server_on()
        if server_was_off:
            _do_power_on()
        return token, server_was_off


def release_token(token_str: str, service: str) -> dict:
    """
    Release a token. If it was the last one, power off the server.

    Holds _shutdown_lock so a concurrent /on can't add a token
    between our "count=0" check and the relay fire.

    Returns a status dict describing what happened.
    """
    with _shutdown_lock:
        token = _remove_token(token_str)

        if token is None:
            return {"status": "ignored", "reason": "token not found"}

        if token.service != service:
            # Token exists but service name doesn't match — log and ignore
            logger.warning(f"Token {token_str} service mismatch: expected {token.service}, got {service}")
            # Put it back
            with _tokens_lock:
                _tokens[token_str] = token
            return {"status": "ignored", "reason": "service name mismatch"}

        remaining = _token_count()
        logger.info(f"Token released: {token_str} ({service})  remaining: {remaining}")

        if remaining == 0 and pins.is_server_on():
            _do_power_off()
            return {"status": "ok", "action": "power_off", "remaining_tokens": 0}

        return {"status": "ok", "action": "none", "remaining_tokens": remaining}

# ── Heartbeat expiry watcher ───────────────────────────────────────────────────

def _heartbeat_watcher():
    """
    Background thread. Checks all tokens every HEARTBEAT_INTERVAL/3 seconds.
    Expires tokens that haven't renewed within EXPIRY_SECONDS.
    Sends Telegram notification for each expired token.
    Does NOT power off the server on expiry.
    """
    logger.info("Heartbeat watcher started")
    while True:
        time.sleep(HEARTBEAT_INTERVAL / 3)
        expired = []
        with _tokens_lock:
            for token_str, token in list(_tokens.items()):
                if token.is_expired():
                    expired.append(_tokens.pop(token_str))

        for token in expired:
            remaining = _token_count()
            logger.warning(f"Token expired: {token.token} ({token.service})")
            notify(
                f"⚠️ Token expired: service \"{token.service}\"\n"
                f"Token: {token.token}\n"
                f"Last seen: {token.age_str()}\n"
                f"Server remains ON. Active tokens remaining: {remaining}"
            )

# ── GPIO17 state watcher ───────────────────────────────────────────────────────

def _gpio17_watcher():
    """
    Background thread. Polls GPIO17 every WATCHER_POLL_S seconds.
    If server goes OFF without the daemon initiating it (Telegram /force,
    physical button press, power cut, crash):
      - Force-clears all active tokens
      - Sends Telegram notification listing cleared services
    Uses debounce: WATCHER_DEBOUNCE consecutive OFF reads before acting.
    Also detects unexpected ON (soft notification only).
    """
    logger.info("GPIO17 watcher started")
    last_known   = pins.is_server_on()
    off_streak   = 0
    on_streak    = 0

    while True:
        time.sleep(WATCHER_POLL_S)
        current = pins.is_server_on()

        if not current:
            off_streak += 1
            on_streak   = 0
        else:
            on_streak  += 1
            off_streak  = 0

        # ── Unexpected OFF ────────────────────────────────────────────────────
        if last_known and off_streak >= WATCHER_DEBOUNCE:
            # Server was ON, now confirmed OFF
            # Only act if there are active tokens (means daemon didn't initiate this)
            active = _all_tokens()
            if active:
                services = _force_clear_all()
                logger.warning(f"Unexpected server OFF detected. Cleared tokens: {services}")
                notify(
                    f"⚠️ Server went OFF unexpectedly.\n"
                    f"Tokens force-cleared: {', '.join(services)}\n"
                    f"Possible cause: manual power off, crash, or Telegram command.\n"
                    f"Server is now OFF."
                )
            else:
                logger.info("Server went OFF (no active tokens — clean or expected)")
            last_known = False

        # ── Unexpected ON ─────────────────────────────────────────────────────
        elif not last_known and on_streak >= WATCHER_DEBOUNCE:
            # Server was OFF, now confirmed ON without daemon initiating it
            if _token_count() == 0:
                logger.info("Server came ON without daemon — likely Telegram /on or physical button")
                notify(
                    "ℹ️ Server came ON without a daemon request.\n"
                    "Likely cause: Telegram /on or physical power button.\n"
                    "No tokens are active."
                )
            last_known = True

# ── FastAPI app ────────────────────────────────────────────────────────────────

app = FastAPI(title="server_ctrl", version="1.0")

# ── Request/response models ────────────────────────────────────────────────────

class OnRequest(BaseModel):
    service: str

class OffRequest(BaseModel):
    token:   str
    service: str

class RenewRequest(BaseModel):
    token: str

# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.post("/on")
def post_on(req: OnRequest):
    """
    Acquire a token. Powers on the server if it was off.
    Returns token immediately — fire and forget, no boot wait.
    """
    if not req.service:
        raise HTTPException(status_code=400, detail="service name required")

    token, fired = acquire_token(req.service)
    logger.info(f"/on  service={req.service}  token={token.token}  fired={fired}")
    return {
        "token":   token.token,
        "service": req.service,
        "fired":   fired,
        "status":  "on",
        "message": "Power pulse sent — server booting" if fired else "Server already on — token issued",
    }


@app.post("/off")
def post_off(req: OffRequest):
    """
    Release a token. Powers off the server if this was the last token.
    Ignored if token not found or service name mismatch.
    """
    result = release_token(req.token, req.service)
    logger.info(f"/off service={req.service}  token={req.token}  result={result}")
    return result


@app.post("/renew")
def post_renew(req: RenewRequest):
    """Heartbeat — reset the expiry timer for this token."""
    with _tokens_lock:
        token = _tokens.get(req.token)

    if token is None:
        raise HTTPException(status_code=404, detail="token not found or already expired")

    token.renew()
    return {"status": "ok", "token": req.token, "expires_in": EXPIRY_SECONDS}


@app.get("/status")
def get_status():
    """Current server state + all active tokens."""
    server_on = pins.is_server_on()
    tokens    = _all_tokens()
    return {
        "server":        "ON" if server_on else "OFF",
        "token_count":   len(tokens),
        "active_tokens": [t.to_dict() for t in tokens],
    }


@app.get("/ping")
def get_ping():
    """Quick ICMP ping to the server."""
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", "2", SERVER_IP],
            capture_output=True, timeout=5
        )
        ok = result.returncode == 0
    except Exception as e:
        return {"ping": "error", "detail": str(e)}
    return {"ping": "ok" if ok else "failed", "host": SERVER_IP}


@app.get("/health")
def get_health():
    """Daemon alive check."""
    return {"status": "ok", "token_count": _token_count()}


# ── Startup ────────────────────────────────────────────────────────────────────

@app.on_event("startup")
def startup():
    threading.Thread(target=_heartbeat_watcher, daemon=True, name="heartbeat").start()
    threading.Thread(target=_gpio17_watcher,    daemon=True, name="gpio17_watch").start()
    logger.info(f"server_ctrl started on port {PORT}")
    logger.info(f"Heartbeat expiry: {EXPIRY_SECONDS}s ({HEARTBEAT_GRACE} missed beats at {HEARTBEAT_INTERVAL}s)")


# ── Main ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
