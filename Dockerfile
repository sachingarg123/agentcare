# PulseDesk — single service: React SPA + FastAPI
FROM node:22-bookworm-slim AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim-bookworm
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock* .python-version ./
RUN uv sync --no-dev --no-install-project

COPY . .
COPY --from=frontend /frontend/dist ./frontend/dist

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

# Persist SQLite / uploads / checkpoints on a Railway volume mounted here
ENV DATABASE_URL=sqlite:////app/data/agentcare.db
ENV CHECKPOINT_DB_PATH=/app/data/checkpoints.db
ENV UPLOAD_DIR=/app/data/uploads

EXPOSE 8000
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
