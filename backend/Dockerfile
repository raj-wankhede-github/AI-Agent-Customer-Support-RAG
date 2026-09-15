# syntax=docker/dockerfile:1.7
# Production image: deterministic dependencies from uv.lock, non-root, no secrets baked in.

FROM python:3.14-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.10 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --extra s3 --no-install-project
COPY alembic.ini gunicorn.conf.py ./
COPY alembic ./alembic
COPY app ./app
COPY seed ./seed
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --extra s3

FROM python:3.14-slim AS runtime
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app \
 && mkdir -p /data/uploads && chown -R app:app /data
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production \
    LOG_JSON=true \
    STORAGE_LOCAL_PATH=/data/uploads \
    INGESTION_WORKER_EMBEDDED=false
COPY --from=builder --chown=app:app /app /app
USER app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=4).status == 200 else 1)"]
# Migrations are NOT run here; run `alembic upgrade head` as a separate one-off task.
CMD ["gunicorn", "-c", "gunicorn.conf.py", "app.main:create_app()"]
