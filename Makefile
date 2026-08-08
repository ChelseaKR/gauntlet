.PHONY: install format lint type test verify package-check demo inventory

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
	uv run gauntlet report demo-results.json --out demo-evidence.md
	uv run gauntlet report demo-results.json --format json --out demo-evidence.json
	uv run gauntlet report demo-results.json --baseline demo-results.json --out demo-evidence-drift.md

inventory:
	uv run gauntlet inventory --update README.md
