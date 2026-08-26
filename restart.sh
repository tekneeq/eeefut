#!/usr/bin/env bash
# Pull latest main, rebuild, and recreate the eeefut-dashboard container.
# Same shape as tekneeq/julia and tekneeq/eeesoc restart.sh.
set -euo pipefail
cd "$(dirname "$0")"

git pull

GIT_SHA="$(git rev-parse --short HEAD)"
GIT_COMMIT_TIME="$(git show -s --format=%cI HEAD)"

mkdir -p data/cache

docker build \
    --build-arg "GIT_SHA=${GIT_SHA}" \
    --build-arg "GIT_COMMIT_TIME=${GIT_COMMIT_TIME}" \
    -t eeefut-dashboard:latest .
docker rm -f eeefut-dashboard 2>/dev/null || true
docker run -d --name eeefut-dashboard --restart unless-stopped \
    -p 8082:8082 \
    -v "$(pwd)/data/cache:/data/cache" \
    -e "EEEFUT_GIT_SHA=${GIT_SHA}" \
    -e "EEEFUT_GIT_COMMIT_TIME=${GIT_COMMIT_TIME}" \
    -e "EEEFUT_SEASON=${EEEFUT_SEASON:-NFL:2025}" \
    -e "EEEFUT_CACHE=/data/cache" \
    eeefut-dashboard:latest

echo "Started eeefut-dashboard at ${GIT_SHA} (${GIT_COMMIT_TIME})"
