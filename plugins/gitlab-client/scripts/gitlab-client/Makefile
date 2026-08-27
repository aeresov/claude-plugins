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

# The full gate: lint, format, types, tests. CI runs this exact target,
# so a green `make check` here is a green CI there.
check:
	uv run ruff check .
	uv run ruff format --check .
	uv run ty check
	uv run pytest -q

update:
	uv lock --upgrade
	uv sync --group dev
