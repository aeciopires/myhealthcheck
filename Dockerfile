# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------- build stage
FROM python:3.15.2-slim-trixie AS build

COPY --from=ghcr.io/astral-sh/uv:0.13.8 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /src

# Dependencies first (better layer caching), then the project itself.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# -------------------------------------------------------------- runtime stage
FROM python:3.15.2-slim-trixie

ARG VERSION=3.0.0
LABEL org.opencontainers.image.title="myhealthcheck" \
      org.opencontainers.image.description="Health check system with Kubernetes discovery" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.authors="Aécio Pires" \
      org.opencontainers.image.source="https://github.com/aeciopires/myhealthcheck" \
      org.opencontainers.image.licenses="GPL-3.0-or-later"

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DB_PATH=/data/myhealthcheck.db

RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app \
 && mkdir -p /data /app/config \
 && chown app:app /data /app

COPY --from=build /opt/venv /opt/venv

WORKDIR /app
USER 10001:10001
EXPOSE 8000
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import os,urllib.request; urllib.request.urlopen(f\"http://127.0.0.1:{os.getenv('HTTP_PORT','8000')}/health/live\", timeout=4)"]

ENTRYPOINT ["myhealthcheck"]
