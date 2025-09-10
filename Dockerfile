# syntax=docker/dockerfile:1
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl ca-certificates && rm -rf /var/lib/apt/lists/*

# Install uv (fast Python package manager)
ENV PATH="/root/.local/bin:${PATH}"
RUN curl -LsSf https://astral.sh/uv/install.sh | sh

COPY pyproject.toml ./
# Install Python deps via uv (project mode). Use a virtualenv and invoke with `uv run`.
RUN uv sync --no-dev

COPY . ./

# Expose port for Railway
EXPOSE 8000

ENV ENV=production OFFLINE_MODE=false

CMD ["uv", "run", "uvicorn", "src.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
