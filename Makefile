.PHONY: setup run dev test lint format migrate revision

PY?=python3

setup:
	uv venv
	uv pip sync requirements.txt

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
