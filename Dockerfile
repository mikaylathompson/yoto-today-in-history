# syntax=docker/dockerfile:1
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends build-essential curl ca-certificates && rm -rf /var/lib/apt/lists/*

# Install uv (fast Python package manager)
ENV PATH="/root/.local/bin:${PATH}"
RUN curl -LsSf https://astral.sh/uv/install.sh | sh

COPY requirements.txt ./
# Install Python deps into system site-packages for slim image
RUN uv pip install --system -r requirements.txt

COPY . ./

# Expose port for Railway
EXPOSE 8000

ENV ENV=production OFFLINE_MODE=false

CMD ["uvicorn", "src.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
