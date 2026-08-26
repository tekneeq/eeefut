#!/usr/bin/env bash
#
# Install the port-80 front door that routes to dashboard containers:
#   / → :8082 (eeefut), /eeesoc/ → :8081, /julia/ → :8501
#
# Julia's stock conf.d file owns ``listen 80 default_server``. This
# script moves that file aside so nginx can reload cleanly.
#
# Usage (on the EC2 host, from the eeefut repo root):
#   ./scripts/install-nginx-80.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/scripts/nginx-eeefut-dashboard.conf"
DEST="/etc/nginx/conf.d/eeefut-dashboard.conf"

if [ ! -f "$SRC" ]; then
    echo "ERROR: missing $SRC" >&2
    exit 1
fi

if [ "$(id -u)" -ne 0 ]; then
    exec sudo -E "$0" "$@"
fi

ts() { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[$(ts)] $*"; }

# Only one default_server may bind :80. Disable known conflicting snippets.
for conf in \
    /etc/nginx/conf.d/julia-dashboard.conf \
    /etc/nginx/conf.d/eeesoc-dashboard.conf
do
    if [ -f "$conf" ] && grep -qE 'listen[[:space:]]+80([[:space:]]|;)' "$conf"; then
        bak="${conf}.disabled"
        log "moving $conf → $bak (it binds :80)"
        mv "$conf" "$bak"
    fi
done

# eeesoc sample listens on 8080 — leave it unless it also claims 80.
install -m 0644 "$SRC" "$DEST"
log "installed $DEST"

nginx -t
if command -v systemctl >/dev/null 2>&1; then
    systemctl reload nginx
else
    nginx -s reload
fi

log "nginx reloaded — http://<host>/ → eeefut :8082"
log "also: /eeesoc/ → :8081   /julia/ → :8501   /health   /dashboards"
