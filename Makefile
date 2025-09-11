.PHONY: setup run dev test lint format migrate revision clean-audio

PY?=python3

setup:
	uv sync --extra dev

run:
	uv run uvicorn src.app.main:app --host 0.0.0.0 --port 8000

dev:
	uv run uvicorn src.app.main:app --reload --port 8000

test:
	uv run pytest -q

lint:
	uv run ruff check src tests

format:
	uv run black src tests

migrate:
	uv run alembic upgrade head

revision:
	uv run alembic revision -m "auto" --autogenerate

clean-audio:
	uv run python scripts/cleanup_audio.py --hours $${HOURS:-$$(uv run python - <<<'from src.app.config import settings; print(settings.audio_retention_hours)')}
