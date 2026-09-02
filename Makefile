.PHONY: install lockfile format lint type test verify package-check demo inventory \
        site pages node-sync htmlvalidate a11y node-audit

install:
	uv sync

# uv.lock must already agree with pyproject.toml before anything else runs.
#
# Every gate below reaches its tool through `uv run`, and a bare `uv run`
# syncs first: when pyproject.toml has moved on, uv relocks, installs the new
# resolution, rewrites uv.lock in the working tree, and then runs the tool,
# which passes. Measured in this repository on 2026-08-29: adding
# `packaging>=24` to pyproject.toml without relocking left `make lint`
# printing "All checks passed!" and exiting 0 while uv.lock's sha256 went from
# e96712df to f04a54e5 on disk. The green result described an environment
# nobody had committed.
#
# `--locked` on every `uv run` refuses to sync a stale lock instead of fixing
# it silently, and this target says so first and on its own, so the failure
# names the lockfile rather than surfacing as a confusing tool error. Offline:
# the check compares the lock against pyproject.toml and needs no index.
lockfile:
	uv lock --check --offline

format:
	uv run --locked ruff format .

lint:
	uv run --locked ruff format --check .
	uv run --locked ruff check .

type:
	uv run --locked mypy

test:
	uv run --locked pytest

verify: lockfile lint type test

package-check:
	uv build

demo:
	uv run --locked gauntlet run --out demo-results.json
	uv run --locked gauntlet report demo-results.json --out demo-evidence.md
	uv run --locked gauntlet report demo-results.json --format json --out demo-evidence.json
	uv run --locked gauntlet report demo-results.json --baseline demo-results.json --out demo-evidence-drift.md

inventory:
	uv run --locked gauntlet inventory --update README.md

# The documentation site. Rendered from the harness: the gate counts come from
# `gauntlet inventory`, and the evidence excerpts are runs made while it builds.
# No network, no clock, and the same commit renders byte-identical pages.
site:
	uv run --locked gauntlet site --out site

# The accessibility gate over the built pages, two ways: html-validate for HTML
# conformance and the markup-level rules, and axe-core in a headless DOM over the
# six tags `tools/a11y.mjs` configures. Contrast is not one of them: axe's
# `color-contrast` needs painted pixels, so it is discarded there and measured
# once, in `test`, off the palette. Structure is checked in both, so `make verify`
# keeps a floor when the node toolchain is not available. What none of this can do
# is look at the pages; README.md says so.
pages: site node-sync htmlvalidate a11y node-audit

node-sync:
	npm ci

htmlvalidate:
	npx html-validate "site/*.html"

a11y:
	node tools/a11y.mjs site

node-audit:
	npm audit --audit-level=high
