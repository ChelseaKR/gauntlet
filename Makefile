.PHONY: install format lint type test verify package-check demo inventory \
        site pages node-sync htmlvalidate a11y node-audit

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

# The documentation site. Rendered from the harness: the gate counts come from
# `gauntlet inventory`, and the evidence excerpts are runs made while it builds.
# No network, no clock, and the same commit renders byte-identical pages.
site:
	uv run gauntlet site --out site

# The WCAG gate over the built pages, two ways: html-validate for HTML conformance
# and the markup-level accessibility rules, and axe-core in a headless DOM for the
# WCAG 2.0/2.1/2.2 A and AA rule sets. Structure and colour contrast are checked
# again in `test`, so `make verify` keeps a floor when the node toolchain is not
# available. What none of this can do is look at the pages; README.md says so.
pages: site node-sync htmlvalidate a11y node-audit

node-sync:
	npm ci

htmlvalidate:
	npx html-validate "site/*.html"

a11y:
	node tools/a11y.mjs site

node-audit:
	npm audit --audit-level=high
