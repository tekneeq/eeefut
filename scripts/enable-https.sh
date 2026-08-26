#!/usr/bin/env bash
#
# Issue a Let's Encrypt cert for eeefut.com and enable nginx :443.
#
# Prerequisites:
#   - DNS A record eeefut.com → this instance's *public* IPv4
#   - Security group inbound 80 and 443
#   - HTTP nginx already installed (./scripts/install-nginx-80.sh)
#
# Usage (on the EC2 host):
#   ./scripts/enable-https.sh
#   CERTBOT_EMAIL=you@example.com ./scripts/enable-https.sh
#   ./scripts/enable-https.sh --www    # also request www.eeefut.com (needs DNS)

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DOMAIN="${EEEFUT_DOMAIN:-eeefut.com}"
CERT_DIR="/etc/letsencrypt/live/${DOMAIN}"
WEBROOT="/var/www/certbot"
WANT_WWW=0

for arg in "$@"; do
    case "$arg" in
        --www) WANT_WWW=1 ;;
        *) echo "Unknown arg: $arg" >&2; exit 2 ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    exec sudo -E "$0" "$@"
fi

ts() { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[$(ts)] $*"; }

"$ROOT/scripts/install-nginx-80.sh"

mkdir -p "$WEBROOT/.well-known/acme-challenge"
chmod -R a+rX /var/www/certbot

if ! command -v certbot >/dev/null 2>&1; then
    log "installing certbot"
    if command -v dnf >/dev/null 2>&1; then
        dnf install -y certbot
    elif command -v yum >/dev/null 2>&1; then
        yum install -y certbot
    else
        echo "ERROR: install certbot (dnf install -y certbot)" >&2
        exit 1
    fi
fi

# Public IPv4 the box thinks it has — compare to DNS.
PUB_IP="$(curl -4 -fsS --max-time 5 https://checkip.amazonaws.com || true)"
DNS_IP="$(python3 -c "import socket; print(socket.getaddrinfo('${DOMAIN}', 80, type=socket.SOCK_STREAM)[0][4][0])" 2>/dev/null || true)"
log "public IPv4=${PUB_IP:-?}  ${DOMAIN} DNS=${DNS_IP:-?}"
if [ -n "$PUB_IP" ] && [ -n "$DNS_IP" ] && [ "$PUB_IP" != "$DNS_IP" ]; then
    log "WARNING: DNS for ${DOMAIN} is ${DNS_IP}, this box's public IP is ${PUB_IP}."
    log "Browsers on the internet will not reach the domain until the A record matches."
fi

CERTBOT_ARGS=(certonly --webroot -w "$WEBROOT" -d "$DOMAIN" --agree-tos --non-interactive --keep-until-expiring)
if [ -n "${CERTBOT_EMAIL:-}" ]; then
    CERTBOT_ARGS+=(--email "$CERTBOT_EMAIL")
else
    CERTBOT_ARGS+=(--register-unsafely-without-email)
fi
if [ "$WANT_WWW" -eq 1 ]; then
    if python3 -c "import socket; socket.getaddrinfo('www.${DOMAIN}', 80)" >/dev/null 2>&1; then
        CERTBOT_ARGS+=(-d "www.${DOMAIN}")
    else
        log "WARNING: www.${DOMAIN} has no DNS A/AAAA record (NXDOMAIN)."
        log "Issuing a cert for ${DOMAIN} only. Add a www A/CNAME in Route53, then re-run with --www."
    fi
fi

log "requesting certificate: ${CERTBOT_ARGS[*]}"
certbot "${CERTBOT_ARGS[@]}"

if [ ! -f "${CERT_DIR}/fullchain.pem" ]; then
    echo "ERROR: certbot finished but ${CERT_DIR}/fullchain.pem is missing" >&2
    exit 1
fi

HOOK="/etc/letsencrypt/renewal-hooks/deploy/eeefut-nginx.sh"
mkdir -p "$(dirname "$HOOK")"
cat > "$HOOK" <<'HOOK'
#!/bin/bash
nginx -t && systemctl reload nginx
HOOK
chmod +x "$HOOK"

if systemctl list-unit-files | grep -q '^certbot-renew.timer'; then
    systemctl enable --now certbot-renew.timer
elif systemctl list-unit-files | grep -q '^certbot.timer'; then
    systemctl enable --now certbot.timer
fi

# Re-run installer so it picks the HTTPS nginx file now that certs exist.
"$ROOT/scripts/install-nginx-80.sh"

log "https://${DOMAIN}/ → eeefut :8082"
curl -fsS "https://${DOMAIN}/health" && echo || log "WARNING: https health failed (SG :443? DNS?)"
