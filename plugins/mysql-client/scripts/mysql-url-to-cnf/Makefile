.PHONY: test
test:
	uv run pytest -q

.PHONY: sync
sync:
	uv sync --group dev

.PHONY: update
update:
	uv lock --upgrade
	uv sync --group dev
