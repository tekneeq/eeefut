#!/usr/bin/env bash
# Container entrypoint: warm NFL cache (cheap when already cached), then serve.
set -euo pipefail

SEASON="${EEEFUT_SEASON:-NFL:2025}"
PORT="${EEEFUT_PORT:-8082}"
HOST="${EEEFUT_HOST:-0.0.0.0}"
CACHE_DIR="${EEEFUT_CACHE:-/data/cache}"

export EEEFUT_CACHE="$CACHE_DIR"
mkdir -p "$EEEFUT_CACHE"

echo "[eeefut] cache=${EEEFUT_CACHE} season=${SEASON} bind=${HOST}:${PORT}"
echo "[eeefut] git=${EEEFUT_GIT_SHA:-unknown} @ ${EEEFUT_GIT_COMMIT_TIME:-unknown}"

uv run eeefut --warm "$SEASON"
exec uv run eeefut --dashboard --host "$HOST" --port "$PORT" --season "$SEASON"
