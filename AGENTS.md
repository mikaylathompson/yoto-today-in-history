# Repository Guidelines

## Project Structure & Module Organization
- Root: `TODAY_IN_HISTORY_SPEC.md` (source of truth for features and edge cases).
- Proposed layout (create as needed):
  - `src/` — core logic (parsing dates, content selection, formatting audio/text).
  - `tests/` — unit/integration tests mirroring `src/` (one test file per module).
  - `data/` — any seed fixtures or static datasets.
  - `scripts/` — maintenance or import/export utilities.
  - `assets/` — non-code artifacts (audio prompts, images).
  - `docs/` — additional documentation.

## Build, Test, and Development Commands
- Prefer a `Makefile` to standardize tasks. Suggested targets:
  - `make setup` — install dependencies for the chosen stack.
  - `make lint` — run format + lint.
  - `make test` — run the full test suite.
  - `make run` — run the app locally (prints or generates “Today in History” output).
- If no Makefile yet, use the stack’s defaults:
  - Python: `uv run pytest` or `pytest`; format with `black`, lint with `ruff`.
  - Node/TS: `npm test`; format with `prettier`, lint with `eslint`.

## Coding Style & Naming Conventions
- Indentation: 2 spaces (JS/TS) or 4 spaces (Python). Keep lines ≤ 100 chars.
- Names: modules/files `kebab-case` (JS/TS) or `snake_case` (Python); classes `PascalCase`; functions/vars `camelCase` (JS/TS) or `snake_case` (Python).
- Always run formatter before committing (Black/Prettier). Fix lint warnings or justify in the PR.

## Testing Guidelines
- Aim for high coverage of date handling, event selection, locale/zone logic, and output formatting.
- Test naming:
  - Python: `tests/test_<module>_*.py`
  - JS/TS: `tests/<module>.spec.ts`
- Run via `make test` (preferred) or framework command. Include representative fixtures in `tests/fixtures/`.

## Commit & Pull Request Guidelines
- Use clear, atomic commits; Conventional Commits are encouraged (e.g., `feat: add leap-year handling`).
- PRs should include:
  - Summary of changes and rationale referencing `TODAY_IN_HISTORY_SPEC.md` sections.
  - Linked issue, screenshots or sample output, and test notes.
  - Checklist: passes `make lint` and `make test`.

## Security & Configuration
- Never commit secrets. Use `.env` and provide `.env.example`.
- Keep datasets free of PII; document sources in `docs/`.
- Pin dependencies and review license compatibility.

## Agent-Specific Notes
- This file applies to the entire repo. Prefer minimal, focused diffs and adhere to the structure above when adding files.
