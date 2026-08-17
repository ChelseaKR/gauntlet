"""The documentation site, rendered from the harness rather than typed out.

Three rules govern this module, and they are the same rules the rest of the
project runs on.

**Counts are counted.** Every number the site prints about the gates comes from
:func:`gauntlet.inventory.build_inventory` over the suites the harness actually
loads, which is the same function ``gauntlet inventory`` and ``make inventory``
use. There is no checked-in copy of the table for a suite change to leave
behind.

**Excerpts are real.** The evidence-pack excerpts on the site are produced at
build time by running the built-in suites against the in-repo toy target, once
healthy and once with a named defect injected, and rendering the result through
:func:`gauntlet.report.render_markdown`. They are output, not illustration.

**Claims stay inside the mapping.** The alignment notice, the sources read, the
identifiers deliberately omitted, and the per-gate framework references are read
from :mod:`gauntlet.mapping`, which cites only identifiers that were read
against their source. The site cannot cite an identifier the mapping refuses to.

The build is deterministic: no clock is consulted unless a generated-on date is
passed in, and the same commit renders byte-identical pages.
"""

from __future__ import annotations

import html
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

from gauntlet.cases import builtin_suites
from gauntlet.drift import results_digest
from gauntlet.evidence import (
    ALIGNMENT_NOTICE,
    CLEAN_RUN_CAVEAT,
    NOT_ESTABLISHED,
    build_evidence_pack,
)
from gauntlet.gates import run_suite
from gauntlet.inventory import Inventory, build_inventory, language_label
from gauntlet.mapping import (
    GATE_MAPPINGS,
    INFORMS_MEANING,
    SELF_TEST_DOCTRINE,
    SOURCES,
    UNVERIFIED_IDENTIFIERS,
    GateMapping,
)
from gauntlet.report import render_markdown
from gauntlet.results import RunResult, run_summary_lines
from gauntlet.toy import GATE_DEFECTS, ToyRag
from gauntlet.toy.target import defects_named

REPO_URL = "https://github.com/ChelseaKR/gauntlet"

SITE_DESCRIPTION = (
    "Documentation for Gauntlet: CI-runnable evaluation gates for generative AI "
    "features, with an evidence pack aligned to California's published GenAI risk "
    "and procurement framework."
)

NO_PACKAGE_NOTICE = (
    "Nothing here is published to PyPI or any other package registry. Install from a "
    "checkout, and pin the GitHub Action to a commit SHA."
)

EXCERPT_DEFECT = "drop_citations"


# ---------------------------------------------------------------------------
# Palette. Held as data so contrast is arithmetic a test can do without a
# browser: tests/test_site.py measures every pair the stylesheet puts together,
# in both themes, against the WCAG 2.2 thresholds.
# ---------------------------------------------------------------------------

LIGHT: dict[str, str] = {
    "surface": "#fbfbf9",
    "surface-raised": "#ffffff",
    "rule": "#e0ded6",
    "rule-strong": "#87857b",
    "ink": "#15150f",
    "ink-2": "#494842",
    "ink-3": "#5f5d56",
    "accent": "#14509c",
    "notice-bg": "#f2efe6",
    "code-bg": "#f5f4ef",
}
"""The light palette, one token per role."""

DARK: dict[str, str] = {
    "surface": "#16161a",
    "surface-raised": "#1f1f24",
    "rule": "#33333a",
    "rule-strong": "#757583",
    "ink": "#f6f6f3",
    "ink-2": "#c4c3bb",
    "ink-3": "#a6a49b",
    "accent": "#93bcf5",
    "notice-bg": "#22222a",
    "code-bg": "#1c1c21",
}
"""The dark palette. Same token names, so no rule needs to know the theme."""


def tokens(palette: Mapping[str, str], *, indent: str = "  ") -> str:
    """One palette as custom-property declarations, in a fixed order."""
    return "".join(f"{indent}--{name}: {value};\n" for name, value in palette.items())


STYLESHEET = (
    f""":root {{
  color-scheme: light;
{tokens(LIGHT)}}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    color-scheme: dark;
{tokens(DARK, indent="    ")}  }}
}}
:root[data-theme="dark"] {{
  color-scheme: dark;
{tokens(DARK)}}}
"""
    + """
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body {
  margin: 0;
  background: var(--surface);
  color: var(--ink);
  font: 400 17px/1.6 ui-sans-serif, system-ui, "Segoe UI", Helvetica, Arial, sans-serif;
}
.wrap { max-width: 58rem; margin: 0 auto; padding: 0 1.4rem 4rem; }
a { color: var(--accent); }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.skip-link {
  position: absolute; left: -9999px; top: 0; z-index: 2;
  background: var(--surface-raised); color: var(--accent);
  padding: .6rem 1rem; border: 1px solid var(--rule-strong);
}
.skip-link:focus { left: .5rem; top: .5rem; }
h1 { font-size: 2.1rem; line-height: 1.15; letter-spacing: -0.02em; margin: 2.4rem 0 .6rem; }
h2 {
  font-size: 1.35rem; letter-spacing: -0.01em; margin: 3rem 0 .5rem;
  padding-top: 1.4rem; border-top: 1px solid var(--rule);
}
h3 { font-size: 1.04rem; margin: 1.8rem 0 .4rem; }
p, li { max-width: 44rem; }
.standfirst { font-size: 1.14rem; color: var(--ink-2); }
.eyebrow {
  font-size: .74rem; letter-spacing: .13em; text-transform: uppercase;
  color: var(--ink-3); margin: 0;
}
nav.site { border-bottom: 1px solid var(--rule); background: var(--surface-raised); }
nav.site .wrap { display: flex; flex-wrap: wrap; align-items: baseline; gap: .4rem 1.1rem; padding: .9rem 1.4rem; }
nav.site .mark { font-weight: 700; letter-spacing: .05em; text-transform: uppercase; font-size: .8rem; }
nav.site a { text-decoration: none; font-size: .92rem; }
nav.site a:hover, nav.site a:focus { text-decoration: underline; }
nav.site a[aria-current="page"] { color: var(--ink); font-weight: 600; text-decoration: underline; }
.notice {
  margin: 1.6rem 0; padding: 1rem 1.2rem; background: var(--notice-bg);
  border: 1px solid var(--rule-strong); border-left-width: 4px;
}
.notice p { margin: 0; max-width: none; }
.notice p + p { margin-top: .6rem; }
.notice .label { font-weight: 700; }
.not-list { padding-left: 1.1rem; }
.not-list > li { margin-bottom: .7rem; }
.scroll { overflow-x: auto; border: 1px solid var(--rule); background: var(--surface-raised); margin: 1.2rem 0; }
table { border-collapse: collapse; width: 100%; font-size: .93rem; }
caption {
  text-align: left; padding: .7rem .8rem; color: var(--ink-2);
  border-bottom: 1px solid var(--rule); font-size: .88rem;
}
th, td { padding: .55rem .8rem; border-bottom: 1px solid var(--rule); vertical-align: top; text-align: left; }
thead th { color: var(--ink-3); font-weight: 600; font-size: .8rem; letter-spacing: .02em; }
tbody th { font-weight: 600; }
tbody tr:last-child th, tbody tr:last-child td { border-bottom: 0; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
code { font-size: .88em; background: var(--code-bg); padding: .1rem .28rem; border-radius: 2px; }
pre {
  overflow-x: auto; background: var(--code-bg); border: 1px solid var(--rule);
  padding: .9rem 1rem; font-size: .84rem; line-height: 1.5; margin: 1.1rem 0;
}
pre code { background: none; padding: 0; font-size: 1em; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(15rem, 1fr)); gap: 1rem; padding: 0; margin: 1.6rem 0; list-style: none; }
.cards li { border: 1px solid var(--rule); background: var(--surface-raised); padding: 1rem 1.1rem 1.1rem; max-width: none; }
.cards h3 { margin: 0 0 .3rem; font-size: 1rem; }
.cards p { font-size: .9rem; color: var(--ink-2); margin: 0; }
footer.site { border-top: 1px solid var(--rule); margin-top: 3.4rem; padding-top: 1.4rem; font-size: .86rem; color: var(--ink-2); }
footer.site p { max-width: 46rem; }
"""
)


# ---------------------------------------------------------------------------
# Small HTML helpers
# ---------------------------------------------------------------------------


def esc(text: str) -> str:
    return html.escape(text, quote=True)


@dataclass(frozen=True)
class Column:
    """One column: its heading, and whether it holds a number."""

    label: str
    numeric: bool = False


@dataclass(frozen=True)
class Table:
    """A data table. Every row is headed by the thing the row is about."""

    slug: str
    caption: str
    columns: tuple[Column, ...]
    rows: tuple[tuple[str, ...], ...]


def render_table(table: Table) -> str:
    """Render a table with a caption, scoped headers, and a keyboard-reachable
    scroll container named by that caption."""
    caption_id = f"cap-{table.slug}"
    head = "".join(
        f'<th scope="col"{' class="num"' if column.numeric else ""}>{esc(column.label)}</th>'
        for column in table.columns
    )
    body = []
    for row in table.rows:
        cells = [f'<th scope="row">{row[0]}</th>']
        cells += [
            f"<td{' class="num"' if column.numeric else ''}>{cell}</td>"
            for column, cell in zip(table.columns[1:], row[1:], strict=True)
        ]
        body.append("<tr>" + "".join(cells) + "</tr>")
    return (
        f'<div class="scroll" tabindex="0" role="group" aria-labelledby="{caption_id}">'
        f'<table><caption id="{caption_id}">{esc(table.caption)}</caption>'
        f"<thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"
    )


def code_block(text: str, *, language: str = "") -> str:
    label = f' data-language="{esc(language)}"' if language else ""
    return f"<pre{label}><code>{esc(text.strip())}</code></pre>"


def paragraphs(*texts: str) -> str:
    return "".join(f"<p>{text}</p>" for text in texts)


def bullets(items: Sequence[str], *, css_class: str = "") -> str:
    attribute = f' class="{css_class}"' if css_class else ""
    return f'<ul{attribute} role="list">' + "".join(f"<li>{item}</li>" for item in items) + "</ul>"


def notice(label: str, *texts: str) -> str:
    body = "".join(
        f"<p>{f'<span class="label">{esc(label)}</span> ' if index == 0 else ''}{text}</p>"
        for index, text in enumerate(texts)
    )
    return f'<div class="notice">{body}</div>'


# ---------------------------------------------------------------------------
# The page skeleton
# ---------------------------------------------------------------------------

PAGES: tuple[tuple[str, str, str], ...] = (
    ("index.html", "Overview", "index"),
    ("gates.html", "Gates", "gates"),
    ("evidence.html", "Evidence pack", "evidence"),
    ("california.html", "California mapping", "california"),
    ("action.html", "GitHub Action", "action"),
)


def page(*, title: str, body: str, active: str, generated: str = "") -> str:
    links = "".join(
        f'<a href="{href}"{' aria-current="page"' if key == active else ""}>{esc(label)}</a>'
        for href, label, key in PAGES
    )
    built = (
        f"<p>Built on {esc(generated)} from the repository at that revision.</p>"
        if generated
        else ""
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(SITE_DESCRIPTION)}">
<style>{STYLESHEET}</style>
</head>
<body>
<a class="skip-link" href="#content">Skip to the content</a>
<nav class="site" aria-label="Documentation sections"><div class="wrap"><span class="mark">Gauntlet</span>{links}</div></nav>
<div class="wrap">
<main id="content">
{body}
</main>
<footer class="site">
<p>{esc(ALIGNMENT_NOTICE)}</p>
<p>{esc(NO_PACKAGE_NOTICE)}</p>
<p>These pages are generated from the harness by <code>gauntlet site</code>: the gate
counts are counted from the suites that load, and the evidence excerpts are output from
runs made while the pages were built. Source at
<a href="{REPO_URL}">{esc(REPO_URL)}</a>. Apache-2.0.</p>
{built}
</footer>
</div>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------


def index_page(inventory: Inventory) -> str:
    counted = (
        f"{len(inventory.gates)} gates and {inventory.total_cases} cases, "
        f"counted from the suites the harness loads"
    )
    what_it_is_not = bullets(
        [
            "<strong>Not a compliance certification.</strong> The language is "
            '"aligned to", never "approved by" or "compliant with". The State of '
            "California, the California Department of Technology, and the Department of "
            "General Services have not reviewed, approved, endorsed, or certified this "
            "project or anything it emits.",
            "<strong>Not a model benchmark.</strong> A gate result describes one deployed "
            "feature in its context: prompts, retrieval, guardrails, routing. It says "
            "nothing about a foundation model in the abstract.",
            "<strong>Not a red-team service.</strong> It is the fixture that keeps "
            "red-team findings regression-tested after the exercise ends.",
            "<strong>Not a way to verify an honest target.</strong> Grounding identifiers "
            "are checked against the context the target <em>claims</em> to have retrieved. "
            "A dishonest target is out of scope, and every evidence pack says so on its "
            "face.",
        ],
        css_class="not-list",
    )
    return "".join(
        [
            '<p class="eyebrow">Documentation</p>',
            "<h1>Gauntlet</h1>",
            '<p class="standfirst">Merge-blocking evaluation gates for generative AI '
            "features, plus an evidence pack that cross-references what the gates found "
            "to California's published GenAI risk and procurement framework.</p>",
            '<h2 id="what-it-is">What it is</h2>',
            paragraphs(
                "Gauntlet runs YAML-driven gate suites against any HTTP endpoint or Python "
                "callable, fails the build when a gate fails, and emits the run in two "
                "forms: a versioned JSON pack a machine can diff, and a document a "
                "reviewer can attach to a risk assessment.",
                f"The built-in suites carry {esc(counted)}. It depends on no model vendor, "
                "reaches the network only where the operator points it at an HTTP target, "
                "and ships a deliberately breakable toy target so a reviewer can watch "
                "each gate fail on purpose.",
            ),
            '<h2 id="what-it-is-not">What it is not</h2>',
            what_it_is_not,
            notice(
                "Alignment, not approval.",
                "Aligned to, not approved or endorsed by, the State of California. "
                'The <a href="california.html">California mapping page</a> sets out what '
                "the mapping claims, what it refuses to claim, and which identifiers were "
                "deliberately left out of it.",
            ),
            '<h2 id="quickstart">Quickstart</h2>',
            "<h3>Install</h3>",
            paragraphs(esc(NO_PACKAGE_NOTICE)),
            code_block(
                f"git clone {REPO_URL}\n"
                "cd gauntlet\n"
                "uv sync\n"
                "\n"
                "# The built-in bilingual suites against the in-repo toy target.\n"
                "uv run gauntlet run --out results.json",
                language="sh",
            ),
            "<h3>Write a case file</h3>",
            paragraphs(
                "Case files are YAML, one gate per file. The loader is strict: unknown "
                "keys, unknown enum values, duplicate ids, and malformed YAML are rejected "
                "with a located error rather than silently skewing a result. English and "
                "Spanish cases are peers, added and changed together.",
            ),
            code_block(
                """
suite: my-grounding
gate: grounding          # grounding | adversarial | refusal | false_positive | golden
version: 1               # bump when the suite changes
threshold: 1.0           # fraction of cases that must pass
cases:
  - id: gnd-en-hours
    language: en
    prompt: What are the library hours?
    expect_grounded: true
    must_contain: ["library"]
  - id: gnd-es-horario
    language: es
    prompt: ¿Cuál es el horario de la biblioteca?
    expect_grounded: true
    must_contain: ["biblioteca"]
""",
                language="yaml",
            ),
            "<h3>Run the gates</h3>",
            code_block(
                "uv run gauntlet run --cases path/to/cases \\\n"
                "  --http-url https://your-service.example/evaluate --out results.json\n"
                "\n"
                "# or a Python target: a factory returning an object with\n"
                "# a name attribute and an ask(prompt, language) method\n"
                "uv run gauntlet run --cases path/to/cases \\\n"
                "  --callable your_package.module:make_target --out results.json",
                language="sh",
            ),
            "<h3>Read the result</h3>",
            paragraphs(
                "The command prints one line per gate and one verdict, then writes the "
                "results JSON. This is the real output of the built-in suites against the "
                "healthy toy target, produced while these pages were built:",
            ),
            code_block("\n".join(run_summary_lines(healthy_run()))),
            paragraphs(
                "<code>gauntlet run</code> exits 1 when any gate misses its threshold, so "
                "it blocks a merge on its own. It exits 2 when the harness itself could "
                "not run, which is a different problem and is reported differently. It "
                "exits 4 when the run cannot be scored: the target returned responses with "
                "nothing readable in them and no loaded suite would have failed it for "
                "that, so a pass rate would be made entirely of checks that silence "
                "satisfies. Turn the results file into the evidence pack with "
                "<code>gauntlet report</code>.",
            ),
            '<h2 id="read-next">Read next</h2>',
            '<ul class="cards" role="list">'
            + "".join(
                f'<li><h3><a href="{href}">{esc(label)}</a></h3><p>{esc(blurb)}</p></li>'
                for href, label, blurb in (
                    (
                        "gates.html",
                        "The gate inventory",
                        "What each gate enforces, the case counts per language, and the "
                        "self-test doctrine that proves every gate can fail.",
                    ),
                    (
                        "evidence.html",
                        "The evidence pack",
                        "What a run emits, with real excerpts, and the limits every pack "
                        "carries on its own face.",
                    ),
                    (
                        "california.html",
                        "The California mapping",
                        "Its purpose, its limits, the identifiers that were read, and the "
                        "identifiers that were deliberately omitted.",
                    ),
                    (
                        "action.html",
                        "The GitHub Action",
                        "Running the gates from another repository, with every input and "
                        "output the action declares.",
                    ),
                )
            )
            + "</ul>",
            '<h2 id="where-this-comes-from">Where this comes from</h2>',
            paragraphs(
                "The discipline is drawn from team-scale platform work on a statewide "
                "platform: a merge-blocking adversarial suite in English and Spanish, "
                "grounding assertions that fail a release when an answer cannot cite its "
                "source, golden-answer regression, and refusal and crisis-routing drills. "
                "The shared safety infrastructure shipped. The assistant it protected did "
                "not launch to residents, because the gates said it was not ready. That "
                "judgment is the product this repository makes reusable. Every line here "
                "is written fresh; no employer code is included.",
            ),
        ]
    )


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------


def inventory_table(inventory: Inventory) -> Table:
    columns = [Column("Gate"), Column("Suite"), Column("Threshold", numeric=True)]
    columns += [Column(language_label(code), numeric=True) for code in inventory.languages]
    columns.append(Column("Total", numeric=True))
    rows: list[tuple[str, ...]] = []
    for gate in inventory.gates:
        rows.append(
            (
                f"<code>{esc(gate.gate)}</code>",
                f"<code>{esc(gate.suite)}</code>",
                f"{gate.threshold * 100:g}%",
                *(str(gate.counts_by_language.get(code, 0)) for code in inventory.languages),
                str(gate.total),
            )
        )
    totals = inventory.totals_by_language
    rows.append(
        (
            "<strong>Total</strong>",
            "",
            "",
            *(str(totals.get(code, 0)) for code in inventory.languages),
            str(inventory.total_cases),
        )
    )
    return Table(
        slug="inventory",
        caption=("Built-in suites, counted by gauntlet inventory. Case counts are per language."),
        columns=tuple(columns),
        rows=tuple(rows),
    )


def gates_page(inventory: Inventory) -> str:
    enforces_rows = tuple(
        (f"<code>{esc(gate.gate)}</code>", esc(gate.enforces))
        for gate in inventory.gates
        if gate.enforces
    )
    defect_rows = tuple(
        (
            f"<code>{esc(gate)}</code>",
            ", ".join(f"<code>{esc(defect)}</code>" for defect in defects),
        )
        for gate, defects in sorted(GATE_DEFECTS.items())
    )
    return "".join(
        [
            '<p class="eyebrow">Gates</p>',
            "<h1>The gate inventory</h1>",
            '<p class="standfirst">Bilingual coverage stated as coverage. Every number '
            "below is counted from the cases the harness loads, and a language absent from "
            "the table is untested.</p>",
            '<h2 id="inventory">What ships, counted</h2>',
            render_table(inventory_table(inventory)),
            paragraphs(
                f"{len(inventory.gates)} gates, {inventory.total_cases} cases. This table "
                "is rendered from <code>gauntlet inventory</code> at the moment the site is "
                "built, the same function that regenerates the block in the repository's "
                "README through <code>make inventory</code>. Adding a case changes this "
                "page without anyone editing it, and no stale copy can survive a build.",
                "Reproduce it with <code>uv run gauntlet inventory</code>, or "
                "<code>uv run gauntlet inventory --format json</code> for the same counts "
                "as data.",
            ),
            '<h2 id="what-each-gate-enforces">What each gate enforces</h2>',
            render_table(
                Table(
                    slug="enforces",
                    caption=(
                        "What each gate enforces, taken from the machine-readable mapping "
                        "the evidence pack cites."
                    ),
                    columns=(Column("Gate"), Column("What it enforces")),
                    rows=enforces_rows,
                )
            ),
            '<h2 id="self-test-doctrine">Self-test doctrine</h2>',
            paragraphs(
                "A check that has never failed is not evidence of health. Gauntlet ships a "
                "deliberately breakable grounded-RAG toy target and, for every gate, a "
                "paired test that injects the exact defect the gate exists to catch and "
                "asserts the gate fails. CI runs those demonstrations on every push, and a "
                "test fails if any gate has no defect that can break it.",
                "The defects are named and enumerated, so the demonstration is a list "
                "rather than a claim. Every gate below has at least one, and the same table "
                "is what the test suite iterates over:",
            ),
            render_table(
                Table(
                    slug="defects",
                    caption=(
                        "Each gate and the named toy defects that must make it fail. "
                        "A gate with no paired defect fails the suite."
                    ),
                    columns=(Column("Gate"), Column("Defects that must break it")),
                    rows=defect_rows,
                )
            ),
            paragraphs(
                "Run the demonstrations yourself. That is the point of shipping them:",
            ),
            code_block(
                "uv run pytest tests/test_self_test_doctrine.py -v\n"
                "\n"
                "# or watch one gate fail end to end, through the action's own path\n"
                "uv run gauntlet run --cases examples/cases \\\n"
                "  --callable examples.broken_target:make_target --out broken.json",
                language="sh",
            ),
            '<h2 id="target-contract">The target contract</h2>',
            paragraphs(
                "A target answers a prompt in a language and reports, honestly, what it "
                "did. Over HTTP the request body is "
                '<code>{"prompt": str, "language": str}</code> and the response body is:',
            ),
            code_block(
                """
{
  "text": "the answer",
  "citations": ["RB-001"],
  "context_ids": ["RB-001", "RB-002"],
  "refused": false,
  "escalated": false
}
""",
                language="json",
            ),
            paragraphs(
                "The harness checks these fields; it never infers them. A Python target is "
                "any object with a <code>name</code> attribute and an "
                "<code>ask(prompt, language)</code> method.",
            ),
        ]
    )


# ---------------------------------------------------------------------------
# Evidence pack
# ---------------------------------------------------------------------------


def healthy_run() -> RunResult:
    """Run the built-in suites against the healthy toy, here and now."""
    return _toy_run(ToyRag())


def broken_run(defect: str = EXCERPT_DEFECT) -> RunResult:
    """The same suites against the toy with one named defect injected."""
    return _toy_run(ToyRag(defects=defects_named(defect)))


def _toy_run(toy: ToyRag) -> RunResult:
    suites = builtin_suites()
    gates = tuple(run_suite(suite, toy) for suite in suites)
    # No clock: the site build must be reproducible, and the evidence pack's
    # digest deliberately excludes the clock anyway.
    return RunResult(target=toy.name, gates=gates, started_at="")


def markdown_section(document: str, heading: str) -> str:
    """One '## ' section of a rendered evidence document, heading included."""
    lines = document.splitlines()
    start = next((index for index, line in enumerate(lines) if line.strip() == heading), None)
    if start is None:
        raise ValueError(f"the rendered evidence document has no section {heading!r}")
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if lines[index].startswith("## ") or lines[index].startswith("---")
        ),
        len(lines),
    )
    return "\n".join(lines[start:end]).strip()


def evidence_page() -> str:
    healthy = healthy_run()
    broken = broken_run()
    clean_pack = build_evidence_pack(healthy.to_dict())
    broken_pack = build_evidence_pack(broken.to_dict(), healthy.to_dict())
    clean_document = render_markdown(clean_pack)
    broken_document = render_markdown(broken_pack)
    digest = results_digest(healthy.to_dict())
    emits = bullets(
        [
            "what was tested: each gate, its suite and version, its threshold, its pass rate",
            "what passed and what failed, with the reason each failing case was rejected",
            "whether a verdict was reached at all: a run the harness refused to score "
            "renders as WITHHELD with the reason, never as a pass",
            "case counts per language, per gate and in total",
            "whole-run drift against a baseline: gates added or removed, pass-rate deltas "
            "per gate and per language, and the cases that newly fail or newly pass",
            "a cross-reference from each gate outcome to the specific SIMM 5305-F items "
            "its results inform, and to the disclosure content it supports",
            "the sources that were read, the identifiers that could not be verified and "
            "are therefore omitted, and what the harness does not establish",
        ]
    )
    return "".join(
        [
            '<p class="eyebrow">Evidence pack</p>',
            "<h1>The evidence pack</h1>",
            '<p class="standfirst">One versioned structure in two forms. The JSON is the '
            "structure; the document is a rendering of the same structure, so they cannot "
            "disagree.</p>",
            '<h2 id="what-it-emits">What it emits</h2>',
            paragraphs("Both forms state, from the run rather than from prose:"),
            emits,
            code_block(
                "uv run gauntlet report results.json --out evidence.md\n"
                "uv run gauntlet report results.json --format json --out evidence.json\n"
                "\n"
                "# whole-run drift against an earlier run\n"
                "uv run gauntlet report results.json \\\n"
                "  --baseline previous-results.json --out evidence.md",
                language="sh",
            ),
            '<h2 id="excerpt-failing">An excerpt from a failing run</h2>',
            paragraphs(
                "The excerpt below is output, not illustration. It was produced while this "
                "page was built, by running the built-in suites against the in-repo toy "
                f"target with the <code>{esc(EXCERPT_DEFECT)}</code> defect injected and "
                "rendering the result through the same reporter a real run uses. It is the "
                "failure section in full, unedited, at the length a real failure runs to.",
            ),
            code_block(markdown_section(broken_document, "## What failed"), language="markdown"),
            paragraphs(
                "The same run, compared against the healthy run as a baseline, reports the "
                "change rather than only the state. The broken toy renames itself after the "
                "defect it carries, which is why the pack notes that the target changed:",
            ),
            code_block(
                markdown_section(broken_document, "## Run-to-run drift"), language="markdown"
            ),
            paragraphs(
                "A run with failures reads through exactly the same sections as a clean "
                "one. There is no path that makes a failure quieter than a pass.",
            ),
            '<h2 id="honesty-guardrails">Honesty guardrails</h2>',
            notice("A clean run is not proof the gates work.", esc(CLEAN_RUN_CAVEAT)),
            paragraphs(
                "That is not a caveat added by this page. It is in the artifact, in the "
                "place a reader is most likely to stop reading. This is the whole of the "
                "clean run's failure section, from the healthy run made while this page was "
                "built:",
            ),
            code_block(markdown_section(clean_document, "## What failed"), language="markdown"),
            paragraphs(
                "Every pack also carries, in the artifact itself, what it does not establish:",
            ),
            bullets([esc(item) for item in NOT_ESTABLISHED], css_class="not-list"),
            '<h2 id="digest">The digest</h2>',
            paragraphs(
                "Each pack carries a <code>results_digest</code>: a sha256 over what the "
                "run observed, with the clock deliberately excluded. Two runs that behaved "
                'identically share a digest, so "nothing changed" is checkable rather than '
                "assumed. The healthy toy run made while this page was built has digest "
                f"<code>{esc(digest)}</code>, and it will have that digest again on any "
                "machine at this revision.",
            ),
        ]
    )


# ---------------------------------------------------------------------------
# California mapping
# ---------------------------------------------------------------------------


def mapping_rows() -> tuple[tuple[str, ...], ...]:
    ordered: list[GateMapping] = [GATE_MAPPINGS[gate] for gate in sorted(GATE_MAPPINGS)]
    ordered.append(SELF_TEST_DOCTRINE)
    rows: list[tuple[str, ...]] = []
    for mapping in ordered:
        references = "".join(
            f"<li><strong>{esc(reference.framework)}</strong>, {esc(reference.locator)}. "
            f"{esc(reference.informs)}</li>"
            for reference in mapping.references
        )
        rows.append(
            (
                f"<code>{esc(mapping.gate)}</code>",
                f'<ul role="list">{references}</ul>' if references else "No verified reference.",
                esc(mapping.disclosure_support),
            )
        )
    return tuple(rows)


def california_page() -> str:
    limits = bullets(
        [
            '<strong>"Informs" is not "satisfies."</strong> A gate produces evidence a '
            "reviewer can attach when answering an item. It never answers the item.",
            "<strong>Only identifiers that were read are cited.</strong> Every citation "
            "was read against its source on the date recorded below. The identifiers that "
            "could not be verified are listed here and in every evidence pack, so their "
            "absence is visibly a choice rather than an oversight. A test fails if an "
            "unverified identifier appears in the mapping.",
            "<strong>A gate that maps to nothing verified says so.</strong> No link is "
            "invented to make the table look complete.",
            "<strong>Nothing here is approval.</strong> A completed SIMM 5305-F is "
            "confidential under the Government Code section cited in its own footer; this "
            "mapping is built from the blank template that CDT publishes.",
            "<strong>If a source revises, the mapping is re-read.</strong> Old citations "
            "are not silently carried forward.",
        ],
        css_class="not-list",
    )
    return "".join(
        [
            '<p class="eyebrow">California mapping</p>',
            "<h1>The California mapping, and its limits</h1>",
            notice("Alignment notice.", esc(ALIGNMENT_NOTICE)),
            '<h2 id="purpose">What it is for</h2>',
            paragraphs(
                "Its purpose is narrow. A vendor making the written contractor disclosure "
                "that SAM 4986.9 requires can attach a Gauntlet run as the testing evidence "
                "behind that disclosure. A state entity filling in the SIMM 5305-F "
                "safeguards items can point at gate outcomes instead of prose assurances.",
                esc(INFORMS_MEANING),
            ),
            '<h2 id="limits">Its limits, enforced rather than promised</h2>',
            limits,
            '<h2 id="the-mapping">The mapping</h2>',
            paragraphs(
                "Each row maps one gate to the items its results inform and to the "
                "disclosure content it supports. The last row is a harness property rather "
                "than a gate. The same table lives in the repository as a machine-readable "
                "module, and it is what the evidence pack cites, so the prose and the code "
                "cannot drift apart.",
            ),
            render_table(
                Table(
                    slug="mapping",
                    caption=(
                        "Gate outcomes and the framework items they inform. Informing an "
                        "item is not satisfying it."
                    ),
                    columns=(
                        Column("Gate"),
                        Column("Items the results inform"),
                        Column("Disclosure content supported"),
                    ),
                    rows=mapping_rows(),
                )
            ),
            '<h2 id="sources-read">Sources read</h2>',
            paragraphs(
                "Every section identifier cited above was read against the source. Where a "
                "source could not be read, the identifier is omitted and listed below "
                "instead of being guessed.",
            ),
            render_table(
                Table(
                    slug="sources",
                    caption="The sources that were read before anything was cited from them.",
                    columns=(
                        Column("Source"),
                        Column("Version read"),
                        Column("How read"),
                        Column("Read on"),
                    ),
                    rows=tuple(
                        (
                            esc(source.name),
                            esc(source.version_read),
                            esc(source.how_read),
                            esc(source.read_on),
                        )
                        for source in SOURCES
                    ),
                )
            ),
            '<h2 id="omitted">Identifiers not verified, therefore omitted</h2>',
            paragraphs(
                "These identifiers appear in the sources above but were not themselves "
                "read. They are listed so their absence from the mapping is visibly a "
                "choice, not an oversight. A test fails if any of them appears in a mapping "
                "row.",
            ),
            render_table(
                Table(
                    slug="omitted",
                    caption="Identifiers deliberately left out of the mapping, and why.",
                    columns=(Column("Identifier"), Column("Why it is omitted")),
                    rows=tuple(
                        (esc(item.identifier), esc(item.why_omitted))
                        for item in UNVERIFIED_IDENTIFIERS
                    ),
                )
            ),
            '<h2 id="correction">A correction made by reading</h2>',
            paragraphs(
                "The scoping document assumed the written disclosure duty lived in SAM "
                "4986.2. Reading the SAM 4986 series shows otherwise: SAM 4986.2 is the "
                'definitions section, where "Material Impact / Materially Impacts" is '
                "defined, and the contractor disclosure duty sits in SAM 4986.9, GenAI "
                "Procurement. The mapping cites the corrected locations. The exact standard "
                "clause wording was not captured verbatim and is paraphrased, never quoted.",
                "The full account, including where the disclosure duty comes from and the "
                "change discipline that applies when a source revises, is in "
                f'<a href="{REPO_URL}/blob/main/docs/california-mapping.md">'
                "docs/california-mapping.md</a> in the repository.",
            ),
        ]
    )


# ---------------------------------------------------------------------------
# GitHub Action
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActionField:
    """One declared input or output of the composite action."""

    name: str
    description: str
    default: str


@dataclass(frozen=True)
class ActionMetadata:
    """The action's declared surface, read from action.yml."""

    inputs: tuple[ActionField, ...]
    outputs: tuple[ActionField, ...]


class ActionMetadataError(ValueError):
    """action.yml could not be read as an action definition."""


def _fields(section: object, source: Path, name: str) -> tuple[ActionField, ...]:
    if not isinstance(section, dict) or not section:
        raise ActionMetadataError(f"{source}: '{name}' is missing or empty")
    fields: list[ActionField] = []
    for key, spec in sorted(section.items()):
        if not isinstance(key, str) or not isinstance(spec, dict):
            raise ActionMetadataError(f"{source}: {name}.{key!r} is not a mapping")
        description = spec.get("description")
        if not isinstance(description, str) or not description.strip():
            raise ActionMetadataError(f"{source}: {name}.{key} has no description")
        default = spec.get("default", "")
        fields.append(
            ActionField(
                name=key,
                description=" ".join(description.split()),
                default="" if default is None else str(default),
            )
        )
    return tuple(fields)


def load_action(path: Path) -> ActionMetadata:
    """Read the action's inputs and outputs from action.yml.

    The site does not restate the action's surface in prose: it reads the same
    file the runner reads, so a renamed input cannot survive a build.
    """
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ActionMetadataError(f"{path}: top level must be a mapping")
    return ActionMetadata(
        inputs=_fields(document.get("inputs"), path, "inputs"),
        outputs=_fields(document.get("outputs"), path, "outputs"),
    )


ACTION_USAGE = """
name: ai-gates

on: [pull_request]

permissions:
  contents: read

jobs:
  gauntlet:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
      - id: gauntlet
        uses: ChelseaKR/gauntlet@<commit-sha>
        with:
          cases: eval/cases
          target-callable: myapp.evalapi:make_target
          baseline: eval/baseline-results.json
      - uses: actions/upload-artifact@330a01c490aca151604b8cf639adc76d48f6c5d4 # v5.0.0
        if: always()
        with:
          name: gauntlet-evidence
          path: |
            gauntlet-results.json
            gauntlet-evidence.md
            gauntlet-evidence.json
      - run: echo "cases ${{ steps.gauntlet.outputs.cases-passed }}/${{ steps.gauntlet.outputs.cases-total }}"
"""


def action_page(action: ActionMetadata) -> str:
    return "".join(
        [
            '<p class="eyebrow">GitHub Action</p>',
            "<h1>Using the GitHub Action</h1>",
            '<p class="standfirst">A composite action usable from any repository. It '
            "installs the harness, runs the gates, writes both forms of the evidence pack, "
            "posts the document to the job summary, and fails the job when a gate "
            "fails.</p>",
            '<h2 id="usage">Usage from another repository</h2>',
            code_block(ACTION_USAGE, language="yaml"),
            paragraphs(
                "Pin the action to a commit SHA, the way the repository pins the actions it "
                "uses itself. Nothing is published to a package registry and no release tag "
                "is implied. From inside this repository the same steps run against a local "
                "checkout with <code>uses: ./</code>.",
                "A failing gate is the expected outcome of a working gate, so the action "
                "does not abort before the evidence pack exists: the gates step captures a "
                "non-zero exit, the pack is built and posted, and a separate step blocks "
                "the merge. Setting <code>fail-on-gate-failure</code> to "
                "<code>false</code> reports without blocking.",
            ),
            '<h2 id="inputs">Inputs</h2>',
            render_table(
                Table(
                    slug="inputs",
                    caption=(
                        "Every input the action declares, read from action.yml at build time."
                    ),
                    columns=(Column("Input"), Column("Default"), Column("Meaning")),
                    rows=tuple(
                        (
                            f"<code>{esc(field.name)}</code>",
                            f"<code>{esc(field.default)}</code>" if field.default else "none",
                            esc(field.description),
                        )
                        for field in action.inputs
                    ),
                )
            ),
            '<h2 id="outputs">Outputs</h2>',
            render_table(
                Table(
                    slug="outputs",
                    caption=(
                        "Every output the action declares, read from action.yml at build time."
                    ),
                    columns=(Column("Output"), Column("Meaning")),
                    rows=tuple(
                        (f"<code>{esc(field.name)}</code>", esc(field.description))
                        for field in action.outputs
                    ),
                )
            ),
            paragraphs(
                "Counts come from the harness. Nothing in the action asserts a number the "
                "run did not produce.",
            ),
        ]
    )


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def render_site(action: ActionMetadata, *, generated: str = "") -> dict[str, str]:
    """Render every page. Pure: the same inputs give byte-identical output."""
    inventory = build_inventory(builtin_suites())
    bodies = {
        "index.html": ("Gauntlet: evaluation gates for GenAI features", index_page(inventory)),
        "gates.html": ("Gate inventory: Gauntlet", gates_page(inventory)),
        "evidence.html": ("The evidence pack: Gauntlet", evidence_page()),
        "california.html": ("The California mapping: Gauntlet", california_page()),
        "action.html": ("The GitHub Action: Gauntlet", action_page(action)),
    }
    return {
        name: page(
            title=title,
            body=body,
            active=key,
            generated=generated,
        )
        for name, _label, key in PAGES
        for title, body in [bodies[name]]
    }


def build_site(out_dir: Path, *, action_file: Path, generated: str = "") -> tuple[Path, ...]:
    """Write the documentation site to ``out_dir`` and return what was written."""
    pages = render_site(load_action(action_file), generated=generated)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, document in pages.items():
        path = out_dir / name
        path.write_text(document, encoding="utf-8")
        written.append(path)
    return tuple(written)
