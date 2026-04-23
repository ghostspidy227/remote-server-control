#!/usr/bin/env bash
# uninstall.sh — Remove power_control from Raspberry Pi
# Usage: sudo ./uninstall.sh

set -euo pipefail

# ── Colours ────────────────────────────────────────────────────────────────────
R="\033[91m"; G="\033[92m"; Y="\033[93m"; B="\033[94m"; C="\033[96m"
DIM="\033[2m"; BOLD="\033[1m"; RST="\033[0m"

ok()   { echo -e "  ${G}✓${RST}  $*"; }
warn() { echo -e "  ${Y}⚠${RST}  $*"; }
err()  { echo -e "  ${R}✗${RST}  $*"; }
info() { echo -e "  ${B}→${RST}  $*"; }
section() { echo -e "\n${BOLD}${C}── $* ${RST}"; }

# ── Root check ─────────────────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    err "This script must be run as root:  sudo ./uninstall.sh"
    exit 1
fi

REAL_USER="${SUDO_USER:-$(logname 2>/dev/null || echo '')}"
if [[ -z "$REAL_USER" ]]; then
    err "Could not determine the real (non-root) user. Run with sudo."
    exit 1
fi

INSTALL_MARKER=".power_control_install"

# ── Banner ─────────────────────────────────────────────────────────────────────
echo -e "
${BOLD}${R}┌─────────────────────────────────────────────────┐
│         power_control  —  uninstall              │
└─────────────────────────────────────────────────┘${RST}"

# ── Detect install ─────────────────────────────────────────────────────────────
INSTALL_DIR=""
for candidate in /opt/power_control "$HOME/power_control"; do
    if [[ -f "$candidate/$INSTALL_MARKER" ]]; then
        INSTALL_DIR="$candidate"
        break
    fi
done

if [[ -z "$INSTALL_DIR" ]]; then
    err "No power_control installation found."
    info "If you installed to a custom directory, remove it manually."
    exit 1
fi

# Read installed user from marker
INSTALLED_USER=$(grep "^installed_user=" "$INSTALL_DIR/$INSTALL_MARKER" 2>/dev/null \
    | cut -d= -f2 | xargs)
[[ -z "$INSTALLED_USER" ]] && INSTALLED_USER="$REAL_USER"

echo -e "\n  ${B}Found installation at:${RST} ${BOLD}$INSTALL_DIR${RST}"
echo -e "  ${B}Installed for user:${RST}   ${BOLD}$INSTALLED_USER${RST}"
echo -e "  ${B}Services:${RST}             server_ctrl, tg_bot"

# ── Confirmation ───────────────────────────────────────────────────────────────
echo -e "
  ${BOLD}${R}This will permanently remove:${RST}
  ${DIM}•  Both systemd services (server_ctrl, tg_bot)
  •  All files in $INSTALL_DIR  (including venv and config)${RST}
"
read -rp "  Are you sure? [y/N] > " confirm
if [[ ! "${confirm,,}" =~ ^y$ ]]; then
    echo -e "\n  ${DIM}Uninstall cancelled.${RST}\n"
    exit 0
fi

# ── Stop + disable services ────────────────────────────────────────────────────
section "Removing systemd services"

for svc in server_ctrl tg_bot; do
    svc_file="/etc/systemd/system/${svc}.service"

    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        systemctl stop "$svc"
        ok "Stopped $svc"
    else
        info "$svc was not running"
    fi

    if systemctl is-enabled --quiet "$svc" 2>/dev/null; then
        systemctl disable "$svc"
        ok "Disabled $svc"
    fi

    if [[ -f "$svc_file" ]]; then
        rm -f "$svc_file"
        ok "Removed $svc_file"
    else
        info "$svc_file not found — skipping"
    fi
done

systemctl daemon-reload
ok "systemd daemon reloaded"

# ── Remove install directory ───────────────────────────────────────────────────
section "Removing files"

if [[ -d "$INSTALL_DIR" ]]; then
    rm -rf "$INSTALL_DIR"
    ok "Removed $INSTALL_DIR"
else
    warn "$INSTALL_DIR not found — already removed?"
fi

# ── Optionally remove from gpio group ─────────────────────────────────────────
section "GPIO group"

if id "$INSTALLED_USER" 2>/dev/null | grep -q "gpio"; then
    echo -e "  ${DIM}User '$INSTALLED_USER' is in the gpio group.${RST}"
    echo -e "  ${DIM}Only remove them if you don't use GPIO for anything else.${RST}\n"
    read -rp "  Remove '$INSTALLED_USER' from the gpio group? [y/N] > " rm_gpio
    if [[ "${rm_gpio,,}" =~ ^y$ ]]; then
        gpasswd -d "$INSTALLED_USER" gpio
        ok "Removed '$INSTALLED_USER' from gpio group"
        warn "They will need to log out and back in for this to take effect"
    else
        info "Left '$INSTALLED_USER' in the gpio group"
    fi
else
    info "User '$INSTALLED_USER' is not in the gpio group — nothing to do"
fi

# ── Done ───────────────────────────────────────────────────────────────────────
echo -e "
${BOLD}${G}┌─────────────────────────────────────────────────┐
│             Uninstall complete ✓                │
└─────────────────────────────────────────────────┘${RST}
  power_control and both services have been removed.
  ${DIM}Python packages were inside the venv — nothing left behind system-wide.${RST}
"
