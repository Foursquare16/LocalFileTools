# Stage 1: build the frontend.
FROM node:26-slim AS frontend
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
COPY NOTICE /NOTICE
RUN npm run build

# Stage 2: runtime for both web and worker (compose picks the command).
FROM python:3.13-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends ghostscript \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.13.8 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/app ./app
COPY --from=frontend /src/dist ./static

# Volume mount points owned by the app user, so fresh named volumes inherit that ownership.
RUN useradd --system --uid 10001 --no-create-home app \
    && mkdir -p /data/state /data/jobs \
    && chown app:app /data/state /data/jobs
USER app
ENV PATH=/app/.venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 LFT_STATIC_DIR=/app/static
