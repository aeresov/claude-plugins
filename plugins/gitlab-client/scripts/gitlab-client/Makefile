.PHONY: sync test tidyup check update

# Install the dev dependency group into the project venv.
sync:
	uv sync --group dev

test:
	uv run pytest -q

# Autoformat + autofix.
tidyup:
	uv run ruff check --fix --unsafe-fixes .
	uv run ruff format .

# Full local gate: lint, format, types, tests. Stricter than CI,
# which runs the tests but not ruff/ty.
check:
	uv run ruff check .
	uv run ruff format --check .
	uv run ty check
	uv run pytest -q

update:
	uv lock --upgrade
	uv sync --group dev
