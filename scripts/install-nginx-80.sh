#!/usr/bin/env bash
#
# Install nginx :80 → this instance's eeefut dashboard on :8082.
# eeefut / eeesoc / julia each run on their own EC2 box.
#
# Amazon Linux ships a ``server { listen 80; server_name _; }`` in
# /etc/nginx/nginx.conf. This script comments that out, installs ours,
# and starts nginx if the unit is inactive.
#
# Usage (on the EC2 host, from the eeefut repo root):
#   ./scripts/install-nginx-80.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/scripts/nginx-eeefut-dashboard.conf"
DEST="/etc/nginx/conf.d/eeefut-dashboard.conf"
MARKER="eeefut: default :80 server disabled"

if [ ! -f "$SRC" ]; then
    echo "ERROR: missing $SRC" >&2
    exit 1
fi

if [ "$(id -u)" -ne 0 ]; then
    exec sudo -E "$0" "$@"
fi

ts() { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[$(ts)] $*"; }

disable_conf_file() {
    local conf="$1"
    [ -f "$conf" ] || return 0
    case "$conf" in
        "$DEST") return 0 ;;
    esac
    if grep -qE 'listen[[:space:]]+(\[::\]:)?80([[:space:]]|;)' "$conf"; then
        local bak="${conf}.disabled"
        log "moving $conf → $bak (it binds :80)"
        mv "$conf" "$bak"
    fi
}

# conf.d / sites-enabled / default.d snippets (not our dest)
shopt -s nullglob
for conf in /etc/nginx/conf.d/*.conf /etc/nginx/default.d/*.conf \
    /etc/nginx/sites-enabled/* /etc/nginx/sites-available/default
do
    disable_conf_file "$conf"
done
shopt -u nullglob

# Amazon Linux / RHEL keep the stock :80 server inside nginx.conf itself.
if [ -f /etc/nginx/nginx.conf ] && grep -qE 'listen[[:space:]]+(\[::\]:)?80([[:space:]]|;)' /etc/nginx/nginx.conf; then
    if grep -q "$MARKER" /etc/nginx/nginx.conf; then
        log "nginx.conf already patched ($MARKER)"
    else
        bak="/etc/nginx/nginx.conf.bak.eeefut"
        if [ ! -f "$bak" ]; then
            cp -a /etc/nginx/nginx.conf "$bak"
            log "backed up /etc/nginx/nginx.conf → $bak"
        fi
        if ! python3 "$ROOT/scripts/disable_nginx_default_80.py" /etc/nginx/nginx.conf; then
            log "WARNING: could not comment the stock :80 server in nginx.conf"
            grep -nE 'listen[[:space:]]+(\[::\]:)?80' /etc/nginx/nginx.conf || true
        fi
    fi
fi

install -m 0644 "$SRC" "$DEST"
log "installed $DEST"

nginx -t

start_or_reload() {
    if command -v systemctl >/dev/null 2>&1; then
        if systemctl is-active --quiet nginx; then
            log "nginx is active — reloading"
            systemctl reload nginx
            return
        fi
        log "nginx.service is not active — enabling and starting"
        systemctl enable nginx >/dev/null 2>&1 || true
        systemctl start nginx
        return
    fi
    if [ -f /run/nginx.pid ] || [ -f /var/run/nginx.pid ]; then
        nginx -s reload
    else
        nginx
    fi
}

start_or_reload

if command -v systemctl >/dev/null 2>&1; then
    systemctl is-active --quiet nginx && log "nginx is active" || {
        log "ERROR: nginx failed to start"
        systemctl status nginx --no-pager || true
        exit 1
    }
fi

log "http://<host>/ → eeefut :8082  (/health → container /health)"

if curl -fsS http://127.0.0.1/health >/dev/null 2>&1; then
    log "health via :80: $(curl -fsS http://127.0.0.1/health | tr -d '\n')"
else
    log "WARNING: curl http://127.0.0.1/health failed — is eeefut-dashboard up on :8082?"
    docker ps --filter name=eeefut-dashboard --format '{{.Names}} {{.Status}}' 2>/dev/null || true
    curl -fsS http://127.0.0.1:8082/health || true
    echo
fi
