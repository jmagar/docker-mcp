# Repository Guidelines

## Project Structure & Module Organization

- `docker_mcp/`: Core package (entrypoint: `docker_mcp.server:main`).
  - `core/` (Docker/SSH ops, transfer, config), `services/` (business logic), `tools/` (MCP tools), `middleware/`, `models/`.
- `tests/`: Pytest suite (unit/integration) and safety checks.
- `config/`: Example configuration and templates.
- `Dockerfile`, `docker-compose.yaml`: Container build/run.

## Build, Test, and Development Commands

- Setup: `uv sync` (installs deps from `pyproject.toml` / `uv.lock`).
- Run server (local): `uv run docker-mcp` (starts FastMCP server on port 8000).
- Run via Docker: `docker compose up -d` (from repo dir) and `docker compose logs`.
- Tests: `uv run pytest` (coverage HTML in `.cache/coverage_html`).
- Quick tests only: `uv run pytest -m "not slow and not integration"`.
- Lint/format: `uv run ruff format . && uv run ruff check . --fix`.
- Type check: `uv run mypy docker_mcp`.

## Coding Style & Naming Conventions

- Python 3.10+, line length 100, spaces indent, double quotes (Ruff configured).
- Modules: snake_case; classes: PascalCase; functions/vars: snake_case; constants: UPPER_SNAKE.
- Keep public APIs typed; internal code gradually typed (see MyPy config).

## Testing Guidelines

- Framework: Pytest (+ `pytest-asyncio`, coverage enabled by default).
- Location/patterns: `tests/test_*.py`; name tests descriptively; prefer small, isolated unit tests.
- Markers: `unit`, `integration`, `slow`, `requires_docker`. Use to scope runs.
- Run safety checker before PRs: `uv run pytest tests/verify_test_safety.py -q`.

## Commit & Pull Request Guidelines

- Use Conventional Commits: `feat: ...`, `fix: ...`, `docs: ...`, `chore: ...` (see `git log`).
- Scope changes narrowly; include tests for bug fixes/features; update docs (README or inline).
- PRs must include: clear description, rationale, usage notes, test results, and any relevant `-m` markers used.
- Link related issues; avoid unrelated refactors; keep diffs focused.

## Security & Configuration Tips

- Do not commit secrets; use `.env` (see `.env.example`).
- SSH-only operations: verify host fingerprints; never add passwords to code or logs.
- Prefer non-destructive commands in examples; call out `--dry-run`/"check" modes where available.
- Local data/logs paths are under `~/.docker-mcp/` when running via installer.

