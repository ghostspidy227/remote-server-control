#!/usr/bin/env bash
# install.sh — Install or update power_control on Raspberry Pi 5
# Usage: sudo ./install.sh

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
    err "This script must be run as root:  sudo ./install.sh"
    exit 1
fi

REAL_USER="${SUDO_USER:-$(logname 2>/dev/null || echo '')}"
if [[ -z "$REAL_USER" ]]; then
    err "Could not determine the real (non-root) user. Run with sudo."
    exit 1
fi
REAL_HOME=$(getent passwd "$REAL_USER" | cut -d: -f6)

# ── Banner ─────────────────────────────────────────────────────────────────────
echo -e "
${BOLD}${C}┌─────────────────────────────────────────────────┐
│       power_control  —  install / update         │
│       Raspberry Pi 5  |  GPIO server control     │
└─────────────────────────────────────────────────┘${RST}"

# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════

INSTALL_MARKER=".power_control_install"   # file written inside install dir

detect_existing_install() {
    # Returns the install dir if a previous install is found, else empty string
    # Check common locations + any dir that has the marker
    for candidate in /opt/power_control "$REAL_HOME/power_control"; do
        if [[ -f "$candidate/$INSTALL_MARKER" ]]; then
            echo "$candidate"
            return
        fi
    done
    echo ""
}

read_config_value() {
    # read_config_value <file> <variable_name>
    # Extracts value from lines like:  VAR = "value"  or  VAR = value
    local file="$1" var="$2"
    grep -E "^${var}\s*=" "$file" 2>/dev/null \
        | head -1 \
        | sed -E 's/^[^=]+=\s*"?([^"#]+)"?\s*(#.*)?$/\1/' \
        | xargs
}

restart_service() {
    local svc="$1"
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        systemctl restart "$svc"
        ok "Restarted $svc"
    fi
}

# ══════════════════════════════════════════════════════════════════════════════
#  HOW-TO GUIDES  (shown inline when user presses H)
# ══════════════════════════════════════════════════════════════════════════════

show_token_help() {
    echo -e "
${BOLD}${Y}  How to get a Telegram Bot Token${RST}
  ${DIM}────────────────────────────────────────────────${RST}
  1. Open Telegram and search for  ${BOLD}@BotFather${RST}
  2. Send:  ${C}/newbot${RST}
  3. Follow the prompts — choose a name and username for your bot
  4. BotFather will reply with a token that looks like:
       ${G}123456789:AAFxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx${RST}
  5. Copy that token and paste it here.
  ${DIM}────────────────────────────────────────────────${RST}"
}

show_userid_help() {
    echo -e "
${BOLD}${Y}  How to get your Telegram User ID${RST}
  ${DIM}────────────────────────────────────────────────${RST}
  1. Open Telegram and search for  ${BOLD}@userinfobot${RST}
  2. Send any message (or just  ${C}/start${RST})
  3. It will reply with your user info — look for:
       ${G}Id: 123456789${RST}
  4. That number is your User ID — paste it here.
  ${DIM}────────────────────────────────────────────────${RST}"
}

# ══════════════════════════════════════════════════════════════════════════════
#  PROMPT HELPERS  (with inline H for help)
# ══════════════════════════════════════════════════════════════════════════════

prompt_bot_token() {
    local token=""
    while true; do
        echo -e "\n  ${BOLD}Telegram Bot Token${RST}" >/dev/tty
        echo -e "  ${DIM}Enter token, or press H for help getting one${RST}" >/dev/tty
        read -rp "  > " token </dev/tty
        if [[ "${token,,}" == "h" ]]; then
            show_token_help >/dev/tty
            continue
        fi
        if [[ "$token" =~ ^[0-9]+:[A-Za-z0-9_-]{30,}$ ]]; then
            echo "$token"
            return
        else
            warn "That doesn't look like a valid token (expected format: 123456:ABC...). Try again." >/dev/tty
        fi
    done
}

prompt_user_id() {
    local uid_val=""
    while true; do
        echo -e "\n  ${BOLD}Telegram User ID${RST}" >/dev/tty
        echo -e "  ${DIM}Enter your numeric user ID, or press H for help finding it${RST}" >/dev/tty
        read -rp "  > " uid_val </dev/tty
        if [[ "${uid_val,,}" == "h" ]]; then
            show_userid_help >/dev/tty
            continue
        fi
        if [[ "$uid_val" =~ ^[0-9]+$ ]]; then
            echo "$uid_val"
            return
        else
            warn "User ID must be a number. Try again." >/dev/tty
        fi
    done
}

prompt_server_ip() {
    local ip=""
    while true; do
        echo -e "\n  ${BOLD}Server IP address${RST}" >/dev/tty
        echo -e "  ${DIM}The local IP of the server you're controlling (e.g. 192.168.1.10)${RST}" >/dev/tty
        read -rp "  > " ip </dev/tty
        if [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
            echo "$ip"
            return
        else
            warn "That doesn't look like a valid IP. Try again." >/dev/tty
        fi
    done
}

prompt_install_dir() {
    local default="/opt/power_control"
    echo -e "\n  ${BOLD}Install directory${RST}" >/dev/tty
    echo -e "  ${DIM}Where to install the project files (default: $default)${RST}" >/dev/tty
    read -rp "  > " dir </dev/tty
    if [[ -z "$dir" ]]; then
        echo "$default"
    else
        echo "$dir"
    fi
}

# ══════════════════════════════════════════════════════════════════════════════
#  PATCH FUNCTIONS  — in-place sed replacements
# ══════════════════════════════════════════════════════════════════════════════

patch_notify_py() {
    local install_dir="$1" token="$2" user_id="$3" server_ip="$4"
    local file="$install_dir/notify.py"

    sed -i "s|BOT_TOKEN\s*=.*|BOT_TOKEN     = \"${token}\"|" "$file"
    sed -i "s|ALLOWED_USERS\s*=.*|ALLOWED_USERS = {${user_id}}|" "$file"
    sed -i "s|SERVER_IP\s*=.*|SERVER_IP     = \"${server_ip}\"|" "$file"
    ok "notify.py patched"
}

patch_sys_path() {
    # Fix hardcoded sys.path.insert lines in server_ctrl.py and tg_bot.py
    local install_dir="$1"
    for f in "$install_dir/server_ctrl.py" "$install_dir/tg_bot.py"; do
        if [[ -f "$f" ]]; then
            sed -i "s|sys\.path\.insert(0,.*)|sys.path.insert(0, \"${install_dir}\")|" "$f"
            ok "Patched sys.path in $(basename $f)"
        fi
    done
}

# ══════════════════════════════════════════════════════════════════════════════
#  SYSTEMD SERVICE INSTALLER
# ══════════════════════════════════════════════════════════════════════════════

write_service() {
    local name="$1" description="$2" script="$3" install_dir="$4" user="$5"
    local service_file="/etc/systemd/system/${name}.service"

    local py="${install_dir}/venv/bin/python3"
    cat > "$service_file" <<EOF
[Unit]
Description=${description}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${user}
WorkingDirectory=${install_dir}
ExecStart=${py} ${install_dir}/${script}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    ok "Wrote $service_file"
}

install_services() {
    local install_dir="$1" user="$2"

    section "Installing systemd services"

    write_service "server_ctrl" \
        "Server Control Daemon (token-based power management)" \
        "server_ctrl.py" "$install_dir" "$user"

    write_service "tg_bot" \
        "Server Power Control Telegram Bot" \
        "tg_bot.py" "$install_dir" "$user"

    systemctl daemon-reload
    systemctl enable server_ctrl tg_bot
    ok "Services enabled (server_ctrl, tg_bot)"
}

# ══════════════════════════════════════════════════════════════════════════════
#  PREFLIGHT CHECKS
# ══════════════════════════════════════════════════════════════════════════════

run_preflight() {
    section "Preflight checks"

    # Raspberry Pi check
    if grep -qi "raspberry pi" /proc/cpuinfo 2>/dev/null || \
       grep -qi "raspberrypi" /sys/firmware/devicetree/base/model 2>/dev/null; then
        ok "Running on Raspberry Pi"
    else
        warn "Could not confirm this is a Raspberry Pi — continuing anyway"
    fi

    # Python version
    PY=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    PY_MAJOR=$(echo $PY | cut -d. -f1)
    PY_MINOR=$(echo $PY | cut -d. -f2)
    if [[ $PY_MAJOR -ge 3 && $PY_MINOR -ge 11 ]]; then
        ok "Python $PY"
    else
        err "Python 3.11+ required, found $PY"
        exit 1
    fi

    # gpio group
    if id "$REAL_USER" 2>/dev/null | grep -q "gpio"; then
        ok "User '$REAL_USER' is in the gpio group"
    else
        warn "User '$REAL_USER' is NOT in the gpio group"
        info "Adding to gpio group (you'll need to log out and back in after install)"
        usermod -aG gpio "$REAL_USER"
        ok "Added '$REAL_USER' to gpio group"
    fi

    # python3-venv available
    if python3 -m venv --help &>/dev/null; then
        ok "python3-venv available"
    else
        info "Installing python3-venv..."
        apt-get install -y python3-venv --quiet \
            && ok "python3-venv installed" \
            || { err "Could not install python3-venv"; exit 1; }
    fi
}

# ══════════════════════════════════════════════════════════════════════════════
#  VENV + DEPENDENCY INSTALLER
# ══════════════════════════════════════════════════════════════════════════════

# Set after install_dir is known — call set_venv_dir <install_dir> first
VENV_DIR=""

set_venv_dir() {
    VENV_DIR="$1/venv"
}

venv_python() {
    echo "$VENV_DIR/bin/python3"
}

venv_pip() {
    echo "$VENV_DIR/bin/pip"
}

create_venv() {
    local install_dir="$1"
    set_venv_dir "$install_dir"

    if [[ -f "$VENV_DIR/bin/python3" ]]; then
        ok "Virtualenv already exists at $VENV_DIR"
        return
    fi

    info "Creating virtualenv at $VENV_DIR"
    python3 -m venv "$VENV_DIR" \
        && ok "Virtualenv created" \
        || { err "Failed to create virtualenv"; exit 1; }
    chown -R "$REAL_USER:$REAL_USER" "$VENV_DIR"
}

install_deps() {
    local install_dir="$1"
    set_venv_dir "$install_dir"

    section "Installing Python dependencies"

    # System lib required by gpiod Python bindings
    if ! dpkg -l libgpiod-dev &>/dev/null; then
        info "Installing libgpiod-dev (required by gpiod Python bindings)..."
        apt-get install -y libgpiod-dev --quiet \
            && ok "libgpiod-dev installed" \
            || warn "Could not install libgpiod-dev — gpiod may fail on this system"
    else
        ok "libgpiod-dev already installed"
    fi

    create_venv "$install_dir"

    local pkgs="gpiod httpx fastapi uvicorn python-telegram-bot websockets"
    info "Installing packages into venv: $pkgs"
    "$(venv_pip)" install $pkgs --quiet \
        && ok "All dependencies installed into venv" \
        || { err "pip install failed — check output above"; exit 1; }
}

check_deps() {
    # Check deps inside the venv. Returns missing package names or empty string.
    if [[ -z "$VENV_DIR" || ! -f "$VENV_DIR/bin/python3" ]]; then
        echo "venv_missing"
        return
    fi
    "$(venv_python)" -c "
import importlib, sys
pkgs = ['gpiod','httpx','fastapi','uvicorn','telegram','websockets']
missing = [p for p in pkgs if importlib.util.find_spec(p) is None]
if missing:
    print(' '.join(missing))
    sys.exit(1)
" 2>/dev/null || true
}

# ══════════════════════════════════════════════════════════════════════════════
#  FRESH INSTALL
# ══════════════════════════════════════════════════════════════════════════════

do_fresh_install() {
    section "Configuration"
    echo -e "  ${DIM}You'll need your Telegram bot token, user ID, and the IP of the server to control.${RST}"

    local install_dir token user_id server_ip

    install_dir=$(prompt_install_dir)
    token=$(prompt_bot_token)
    user_id=$(prompt_user_id)
    server_ip=$(prompt_server_ip)

    # ── Copy files ─────────────────────────────────────────────────────────────
    section "Copying files"

    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

    if [[ "$SCRIPT_DIR" == "$install_dir" ]]; then
        info "Script is already in target directory — skipping copy"
    else
        mkdir -p "$install_dir/server_gpio"
        cp -r "$SCRIPT_DIR"/*.py "$install_dir/" 2>/dev/null || true
        cp -r "$SCRIPT_DIR/server_gpio/"*.py "$install_dir/server_gpio/" 2>/dev/null || true
        ok "Files copied to $install_dir"
    fi

    # Fix ownership
    chown -R "$REAL_USER:$REAL_USER" "$install_dir"
    chmod 750 "$install_dir"

    # ── Patch config ───────────────────────────────────────────────────────────
    section "Patching configuration"
    patch_notify_py "$install_dir" "$token" "$user_id" "$server_ip"
    patch_sys_path "$install_dir"

    # ── Deps ───────────────────────────────────────────────────────────────────
    install_deps "$install_dir"

    # ── Systemd ────────────────────────────────────────────────────────────────
    install_services "$install_dir" "$REAL_USER"

    # ── Write install marker ───────────────────────────────────────────────────
    cat > "$install_dir/$INSTALL_MARKER" <<EOF
install_dir=${install_dir}
venv_dir=${install_dir}/venv
installed_user=${REAL_USER}
installed_at=$(date -Iseconds)
EOF
    chown "$REAL_USER:$REAL_USER" "$install_dir/$INSTALL_MARKER"

    # ── Done ───────────────────────────────────────────────────────────────────
    echo -e "
${BOLD}${G}┌─────────────────────────────────────────────────┐
│              Install complete! ✓                │
└─────────────────────────────────────────────────┘${RST}
  ${B}Install dir:${RST}  $install_dir
  ${B}Services:${RST}     server_ctrl.service, tg_bot.service
  ${B}User:${RST}         $REAL_USER

  ${BOLD}Next steps:${RST}
  1. Start services:
       ${C}sudo systemctl start server_ctrl tg_bot${RST}
  2. Check they're running:
       ${C}sudo systemctl status server_ctrl tg_bot${RST}
  3. Open Telegram and send ${C}/start${RST} to your bot
  4. If you just got added to the gpio group, log out and back in.

  ${DIM}Run this script again anytime to update config or fix issues.${RST}
"
}

# ══════════════════════════════════════════════════════════════════════════════
#  HEALTH CHECK  — used by re-run to show what's broken/ok
# ══════════════════════════════════════════════════════════════════════════════

run_health_check() {
    local install_dir="$1"
    local all_ok=true

    section "Health check"

    # Files
    local missing_files=()
    for f in notify.py server_ctrl.py tg_bot.py gpiosim.py server_gpio/__init__.py \
              server_gpio/pins.py server_gpio/power.py server_gpio/monitor.py \
              server_gpio/watchdog.py server_gpio/ws_agent.py; do
        [[ -f "$install_dir/$f" ]] || missing_files+=("$f")
    done
    if [[ ${#missing_files[@]} -eq 0 ]]; then
        ok "All project files present"
    else
        err "Missing files: ${missing_files[*]}"
        all_ok=false
    fi

    # Config values in notify.py
    local nf="$install_dir/notify.py"
    local cur_token cur_uid cur_ip
    cur_token=$(read_config_value "$nf" "BOT_TOKEN")
    cur_uid=$(read_config_value "$nf" "ALLOWED_USERS")
    cur_ip=$(read_config_value "$nf" "SERVER_IP")

    if [[ "$cur_token" == "yourbottoken" || -z "$cur_token" ]]; then
        err "Bot token is not configured (still placeholder)"
        all_ok=false
    else
        ok "Bot token:   ${DIM}${cur_token:0:12}...${RST}"
    fi

    if [[ "$cur_uid" == *"user1"* || "$cur_uid" == *"user2"* || -z "$cur_uid" ]]; then
        err "Allowed users not configured (still placeholder)"
        all_ok=false
    else
        ok "User IDs:    ${DIM}${cur_uid}${RST}"
    fi

    if [[ "$cur_ip" == "serverip" || -z "$cur_ip" ]]; then
        err "Server IP not configured (still placeholder)"
        all_ok=false
    else
        ok "Server IP:   ${DIM}${cur_ip}${RST}"
    fi

    # sys.path in server_ctrl.py and tg_bot.py
    for f in server_ctrl.py tg_bot.py; do
        if grep -q "sys\.path\.insert" "$install_dir/$f" 2>/dev/null; then
            local path_val
            path_val=$(grep "sys\.path\.insert" "$install_dir/$f" | head -1)
            if echo "$path_val" | grep -q "$install_dir"; then
                ok "sys.path in $f: ok"
            else
                warn "sys.path in $f may point to wrong directory"
                all_ok=false
            fi
        fi
    done

    # Venv
    set_venv_dir "$install_dir"
    if [[ -f "$VENV_DIR/bin/python3" ]]; then
        ok "Virtualenv present"
    else
        err "Virtualenv missing at $VENV_DIR"
        all_ok=false
    fi

    # Dependencies
    local missing_deps
    missing_deps=$(check_deps 2>/dev/null || true)
    if [[ -z "$missing_deps" ]]; then
        ok "Python dependencies: all installed"
    else
        err "Missing Python packages: $missing_deps"
        all_ok=false
    fi

    # Systemd services
    for svc in server_ctrl tg_bot; do
        if [[ -f "/etc/systemd/system/${svc}.service" ]]; then
            if systemctl is-active --quiet "$svc"; then
                ok "Service $svc: running"
            else
                warn "Service $svc: installed but not running"
                all_ok=false
            fi
        else
            err "Service $svc: not installed"
            all_ok=false
        fi
    done

    if $all_ok; then
        echo -e "\n  ${G}${BOLD}Everything looks good.${RST}"
    else
        echo -e "\n  ${Y}${BOLD}Some issues found — use the update menu to fix them.${RST}"
    fi

    echo "$all_ok"
}

# ══════════════════════════════════════════════════════════════════════════════
#  UPDATE MENU  — shown on re-run
# ══════════════════════════════════════════════════════════════════════════════

do_update() {
    local install_dir="$1"
    set_venv_dir "$install_dir"

    echo -e "\n  ${B}Existing install found:${RST} $install_dir"

    local health
    health=$(run_health_check "$install_dir")
    local all_ok="${health##*$'\n'}"   # last line from run_health_check

    echo -e "
  ${BOLD}What would you like to do?${RST}

  ${C}1${RST}  Change bot token
  ${C}2${RST}  Change server IP
  ${C}3${RST}  Add a Telegram user ID
  ${C}4${RST}  Remove a Telegram user ID
  ${C}5${RST}  Re-install / fix Python dependencies
  ${C}6${RST}  Re-install / fix systemd services
  ${C}7${RST}  Fix broken parts (auto-repair from health check)
  ${C}8${RST}  Full re-install (keeps your config)
  ${C}q${RST}  Quit
"
    read -rp "  > " choice

    local nf="$install_dir/notify.py"

    case "$choice" in

    1)  # Change bot token
        local new_token
        new_token=$(prompt_bot_token)
        sed -i "s|BOT_TOKEN\s*=.*|BOT_TOKEN     = \"${new_token}\"|" "$nf"
        ok "Bot token updated"
        restart_service server_ctrl
        restart_service tg_bot
        ;;

    2)  # Change server IP
        local new_ip
        new_ip=$(prompt_server_ip)
        sed -i "s|SERVER_IP\s*=.*|SERVER_IP     = \"${new_ip}\"|" "$nf"
        ok "Server IP updated"
        restart_service server_ctrl
        restart_service tg_bot
        ;;

    3)  # Add user ID
        local new_uid
        new_uid=$(prompt_user_id)
        # ALLOWED_USERS = {123} → {123, 456}
        sed -i "s|ALLOWED_USERS\s*=\s*{\(.*\)}|ALLOWED_USERS = {\1, ${new_uid}}|" "$nf"
        ok "User ID $new_uid added"
        restart_service tg_bot
        ;;

    4)  # Remove user ID
        local cur_uids
        cur_uids=$(read_config_value "$nf" "ALLOWED_USERS")
        echo -e "\n  Current user IDs: ${C}${cur_uids}${RST}"
        echo -e "  ${DIM}Enter the User ID to remove:${RST}"
        read -rp "  > " rm_uid
        if [[ -z "$rm_uid" ]]; then
            warn "Nothing entered — no change made"
        else
            # Remove ', 123' or '123, ' or just '123' from the set
            sed -i "s|,\s*${rm_uid}||; s|${rm_uid}\s*,\s*||; s|${rm_uid}||" "$nf"
            ok "User ID $rm_uid removed (if it was present)"
            restart_service tg_bot
        fi
        ;;

    5)  install_deps "$install_dir" ;;

    6)  install_services "$install_dir" "$REAL_USER" ;;

    7)  # Auto-repair broken parts
        section "Auto-repair"

        # Missing files — nothing we can do without source, warn
        for f in notify.py server_ctrl.py tg_bot.py; do
            if [[ ! -f "$install_dir/$f" ]]; then
                err "Missing $f — cannot auto-repair, re-run full install from source dir"
            fi
        done

        # Placeholder config values
        local cur_token cur_uid cur_ip
        cur_token=$(read_config_value "$nf" "BOT_TOKEN")
        cur_uid=$(read_config_value "$nf" "ALLOWED_USERS")
        cur_ip=$(read_config_value "$nf" "SERVER_IP")

        if [[ "$cur_token" == "yourbottoken" || -z "$cur_token" ]]; then
            warn "Bot token is placeholder — please enter a real token"
            local new_tok; new_tok=$(prompt_bot_token)
            sed -i "s|BOT_TOKEN\s*=.*|BOT_TOKEN     = \"${new_tok}\"|" "$nf"
            ok "Bot token fixed"
        fi
        if [[ "$cur_uid" == *"user1"* || "$cur_uid" == *"user2"* || -z "$cur_uid" ]]; then
            warn "User IDs are placeholder — please enter a real user ID"
            local new_uid; new_uid=$(prompt_user_id)
            sed -i "s|ALLOWED_USERS\s*=.*|ALLOWED_USERS = {${new_uid}}|" "$nf"
            ok "User ID fixed"
        fi
        if [[ "$cur_ip" == "serverip" || -z "$cur_ip" ]]; then
            warn "Server IP is placeholder — please enter a real IP"
            local new_ip; new_ip=$(prompt_server_ip)
            sed -i "s|SERVER_IP\s*=.*|SERVER_IP     = \"${new_ip}\"|" "$nf"
            ok "Server IP fixed"
        fi

        # sys.path
        patch_sys_path "$install_dir"

        # Missing deps
        local missing_deps
        missing_deps=$(check_deps 2>/dev/null || true)
        if [[ -n "$missing_deps" ]]; then
            info "Installing missing packages: $missing_deps"
            install_deps "$install_dir"
        fi

        # Missing services
        for svc_pair in "server_ctrl:server_ctrl.py:Server Control Daemon (token-based power management)" \
                        "tg_bot:tg_bot.py:Server Power Control Telegram Bot"; do
            IFS=: read -r svc script desc <<< "$svc_pair"
            if [[ ! -f "/etc/systemd/system/${svc}.service" ]]; then
                write_service "$svc" "$desc" "$script" "$install_dir" "$REAL_USER"
            fi
        done
        systemctl daemon-reload
        systemctl enable server_ctrl tg_bot 2>/dev/null || true

        ok "Auto-repair complete"
        restart_service server_ctrl
        restart_service tg_bot
        ;;

    8)  # Full re-install, keep config
        section "Full re-install (preserving config)"

        # Snapshot existing config before overwriting
        local saved_token saved_uid saved_ip
        saved_token=$(read_config_value "$nf" "BOT_TOKEN")
        saved_uid=$(read_config_value "$nf" "ALLOWED_USERS")
        saved_ip=$(read_config_value "$nf" "SERVER_IP")

        SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
        if [[ "$SCRIPT_DIR" != "$install_dir" ]]; then
            cp -r "$SCRIPT_DIR"/*.py "$install_dir/" 2>/dev/null || true
            cp -r "$SCRIPT_DIR/server_gpio/"*.py "$install_dir/server_gpio/" 2>/dev/null || true
            chown -R "$REAL_USER:$REAL_USER" "$install_dir"
            ok "Files refreshed from $SCRIPT_DIR"
        else
            info "Running from install dir — files not re-copied"
        fi

        # Restore config that got overwritten by fresh files
        patch_notify_py "$install_dir" "$saved_token" "$saved_uid" "$saved_ip"
        patch_sys_path "$install_dir"
        install_deps "$install_dir"
        install_services "$install_dir" "$REAL_USER"

        systemctl restart server_ctrl tg_bot 2>/dev/null && ok "Services restarted" || true
        ok "Full re-install complete"
        ;;

    q|Q) echo -e "\n  ${DIM}Bye.${RST}\n"; exit 0 ;;
    *)   warn "Unknown option '$choice'" ;;
    esac
}

# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

EXISTING=$(detect_existing_install)

if [[ -n "$EXISTING" ]]; then
    do_update "$EXISTING"
else
    run_preflight
    do_fresh_install
fi
