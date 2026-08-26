# Matches + Similar dashboard in a Debian 12 container (same pattern as julia / eeesoc).
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

# Layer 1: deps only (cached unless lockfile changes).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

# Layer 2: project source + install.
COPY src ./src
COPY scripts ./scripts
COPY README.md ./
RUN uv sync --frozen \
    && chmod +x scripts/docker-entrypoint.sh

# Persist warm cache across container recreates (host-mounted in restart.sh).
VOLUME ["/data/cache"]

EXPOSE 8082

ARG GIT_SHA=unknown
ARG GIT_COMMIT_TIME=unknown
ENV EEEFUT_GIT_SHA=$GIT_SHA \
    EEEFUT_GIT_COMMIT_TIME=$GIT_COMMIT_TIME \
    EEEFUT_HOST=0.0.0.0 \
    EEEFUT_PORT=8082 \
    EEEFUT_SEASON=NFL:2025 \
    EEEFUT_CACHE=/data/cache \
    UV_NATIVE_TLS=true \
    PYTHONUNBUFFERED=1

CMD ["./scripts/docker-entrypoint.sh"]
