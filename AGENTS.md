# Repository Guidelines

## Project Structure & Module Organization
- `docker_mcp/`: core server package. `services/` orchestrates multi-host operations, `tools/` exposes MCP endpoints, `models/` defines Pydantic schemas, and `core/` manages Docker/SSH orchestration.
- `tests/`: pytest suite mirroring package layout; favors deterministic FastMCP client flows for tool coverage.
- `config/`: sample host inventories (`hosts.example.yml`) and local overrides; do not commit real credentials.
- `docs/`: architecture, testing, and operational playbooks referenced by contributors.
- `scripts/`: automation helpers (e.g. code-health analysis) meant to run via `uv run python`.
- `logs/`: runtime artifacts kept out of version control; safe scratch space for local debugging.

## Build, Test, and Development Commands
- `uv sync --dev`: install project and development dependencies from `pyproject.toml`.
- `uv run docker-mcp --config config/hosts.example.yml`: launch the MCP server locally with sample host configuration.
- `uv run pytest` / `uv run pytest -k "not slow"`: execute the full suite or skip port-scanning tests; coverage HTML lands in `.cache/coverage_html`.
- `uv run ruff format .` followed by `uv run ruff check . --fix`: apply formatting, then lint and auto-fix style issues.
- `uv run mypy docker_mcp`: run type checks against the core package before opening a PR.

## Coding Style & Naming Conventions
- Target Python 3.11+ with type hints on public interfaces and async pathways.
- Respect Ruff configuration: 100-character lines, double quotes, and space indentation.
- Modules, functions, and variables stay `snake_case`; classes use `PascalCase`; constants remain `UPPER_SNAKE`.
- Preserve the existing service → tool → resource layering; route subprocess access through established helpers in `docker_mcp/core` and `docker_mcp/services`.

## Testing Guidelines
- Create tests under `tests/` with files named `test_<feature>.py`; mirror new modules with matching coverage.
- Use markers such as `@pytest.mark.integration`, `slow`, and `requires_docker` to keep CI selectors meaningful.
- Prefer FastMCP in-memory clients for unit tests and mock external SSH/Docker interactions to stay deterministic.
- Update shared fixtures in `tests/conftest.py` when introducing new services or configuration knobs.

## Commit & Pull Request Guidelines
- Follow conventional commit prefixes (`feat:`, `fix:`, `refactor:`) as used across the history; keep scopes concise and actionable.
- Reference related issues or PRs in commit/PR bodies and explain operational, config, or migration impacts.
- PRs should summarize behavior changes, list validation commands (`uv run pytest …`, `uv run mypy …`), and attach logs or screenshots for UI/operational adjustments.
- Highlight breaking changes, host requirements, or security considerations in a dedicated PR section.

## Security & Configuration Tips
- Never commit real host inventories or SSH material; rely on `hosts.example.yml` for documentation.
- Scrub sensitive paths from artifacts in `logs/` before sharing.
- Reuse existing permission and validation utilities when extending services that trigger remote execution.
