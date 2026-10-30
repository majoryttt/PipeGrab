# AGENTS.md

## Health Stack

- typecheck: .venv/bin/mypy bot
- lint: .venv/bin/ruff check .
- test: .venv/bin/pytest -o cache_dir=.venv/.pytest_cache
- deadcode: .venv/bin/vulture bot
