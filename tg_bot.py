import sys
import os
sys.path.insert(0, "/dir")

import threading
import subprocess
import logging

from server_gpio import monitor, ServerState, power, pins
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from notify import BOT_TOKEN, ALLOWED_USERS, SERVER_IP, notify_async

logging.basicConfig(level=logging.WARNING)

# ── Auth ───────────────────────────────────────────────────────────────────────

def is_authorized(user_id: int) -> bool:
    return user_id in ALLOWED_USERS

# ── Action lock — only one power action at a time ─────────────────────────────

_action_lock = threading.Lock()

def _run_exclusive(fn, *args, **kwargs) -> bool:
    if not _action_lock.acquire(blocking=False):
        return False
    def _task():
        try:
            fn(*args, **kwargs)
        finally:
            _action_lock.release()
    threading.Thread(target=_task, daemon=True).start()
    return True

def _busy_msg() -> str:
    return "⚠️ Another power action is already running, wait for it to finish."

# ── Handlers ──────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    await update.message.reply_text(
        "🤖 Server bot alive.\n\n"
        "/status — server state + ping\n"
        "/ping   — quick ping check\n"
        "/on     — short press (power on)\n"
        "/off    — short press (soft power off)\n"
        "/force  — 4s hold (hard power off)\n"
        "/reboot — soft off → on"
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    try:
        state = monitor.current_state(host=SERVER_IP)
        msgs = {
            ServerState.ON:      "🟢 Server: ON (ping OK)",
            ServerState.OFF:     "🔴 Server: OFF",
            ServerState.BOOTING: "🟡 Server: BOOTING (LED on, ping not yet up)",
            ServerState.HUNG:    "⚠️ Server: HUNG (LED on, ping failing)",
            ServerState.UNKNOWN: "❓ Server: UNKNOWN",
        }
        await update.message.reply_text(msgs.get(state, "❓ Unknown state"))
    except Exception as e:
        await update.message.reply_text(f"Error reading state: {e}")

async def ping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", "2", SERVER_IP],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        msg = "🌐 Ping: OK" if result.returncode == 0 else "❌ Ping: FAILED"
    except Exception as e:
        msg = f"Error: {e}"
    await update.message.reply_text(msg)

async def power_on_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    if pins.is_server_on():
        await update.message.reply_text("🟢 Server is already ON")
        return
    if not _run_exclusive(pins.pulse_relay, 0.5):
        await update.message.reply_text(_busy_msg())
        return
    await update.message.reply_text("⚡ Power button pressed (turning ON)")

async def power_off_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    if not pins.is_server_on():
        await update.message.reply_text("🔴 Server is already OFF")
        return
    if not _run_exclusive(pins.pulse_relay, 0.5):
        await update.message.reply_text(_busy_msg())
        return
    await update.message.reply_text("🛑 Power button pressed (soft OFF)")

async def force_off_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    if not pins.is_server_on():
        await update.message.reply_text("🔴 Server is already OFF (nothing to force)")
        return
    if not _run_exclusive(pins.pulse_relay, 4.0):
        await update.message.reply_text(_busy_msg())
        return
    await update.message.reply_text("☠️ Force power off (4s hold)...")

async def reboot_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update.effective_user.id):
        return
    if not pins.is_server_on():
        await update.message.reply_text("🔴 Server is OFF — cannot reboot")
        return
    if not _run_exclusive(power.reboot, host=SERVER_IP, force_off=False):
        await update.message.reply_text(_busy_msg())
        return
    await update.message.reply_text("🔄 Rebooting (power cycle)...")

# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",  start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("ping",   ping_cmd))
    app.add_handler(CommandHandler("on",     power_on_cmd))
    app.add_handler(CommandHandler("off",    power_off_cmd))
    app.add_handler(CommandHandler("force",  force_off_cmd))
    app.add_handler(CommandHandler("reboot", reboot_cmd))
    print("Bot running...")
    app.run_polling()

if __name__ == "__main__":
    main()
