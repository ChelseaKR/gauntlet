.PHONY: install format lint type test verify package-check demo

install:
	uv sync

format:
	uv run ruff format .

lint:
	uv run ruff format --check .
	uv run ruff check .

type:
	uv run mypy

test:
	uv run pytest

verify: lint type test

package-check:
	uv build

demo:
	uv run gauntlet run --out demo-results.json
	uv run gauntlet report demo-results.json
