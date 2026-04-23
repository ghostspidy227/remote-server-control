"""
notify.py — Shared Telegram push notification module.

Used by both tg_bot.py and server_ctrl.py.
Neither file defines BOT_TOKEN or ALLOWED_USERS themselves — they import from here.

Usage (sync context, e.g. server_ctrl.py background thread):
    from notify import notify
    notify("⚠️ Something happened")

Usage (async context, e.g. inside tg_bot.py handlers):
    from notify import notify_async
    await notify_async("⚠️ Something happened")
"""

import logging
import asyncio
import threading

import httpx

logger = logging.getLogger(__name__)

# ── Config — edit these ────────────────────────────────────────────────────────

BOT_TOKEN     = "yourbottoken"
ALLOWED_USERS = {user1, user2}   # telegram user IDs to notify
SERVER_IP     = "serverip"         # also centralised here

# ── Internal ───────────────────────────────────────────────────────────────────

_TG_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"


def _send_blocking(chat_id: int, text: str) -> bool:
    """Send a single message synchronously. Returns True on success."""
    try:
        r = httpx.post(_TG_URL, json={"chat_id": chat_id, "text": text}, timeout=10)
        if r.status_code != 200:
            logger.warning(f"notify: Telegram returned {r.status_code} for chat {chat_id}")
            return False
        return True
    except Exception as e:
        logger.error(f"notify: failed to send to {chat_id}: {e}")
        return False


# ── Public API ─────────────────────────────────────────────────────────────────

def notify(message: str) -> None:
    """
    Send a Telegram message to all ALLOWED_USERS.
    Synchronous — safe to call from any thread.
    Runs sends in a background thread so it never blocks the caller.
    """
    def _task():
        for uid in ALLOWED_USERS:
            _send_blocking(uid, message)
    threading.Thread(target=_task, daemon=True).start()


async def notify_async(message: str) -> None:
    """
    Send a Telegram message to all ALLOWED_USERS.
    Async version — use inside async functions (e.g. tg_bot handlers).
    """
    async with httpx.AsyncClient() as client:
        for uid in ALLOWED_USERS:
            try:
                r = await client.post(
                    _TG_URL,
                    json={"chat_id": uid, "text": message},
                    timeout=10
                )
                if r.status_code != 200:
                    logger.warning(f"notify_async: Telegram returned {r.status_code} for {uid}")
            except Exception as e:
                logger.error(f"notify_async: failed to send to {uid}: {e}")
