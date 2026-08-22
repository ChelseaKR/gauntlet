"""What can be checked about the documentation site without a browser, checked.

Four things are gated here, none of them needing a renderer:

* **Structure.** The pages are parsed and the document facts a screen reader
  depends on are asserted: one ``h1``, no skipped heading level, every table
  header scoped, every table named, no repeated id, one main landmark, a
  language on the root element, a skip link that points at something.
  ``html-validate`` and ``axe-core`` cover the same ground more thoroughly in
  ``make pages``; these run inside ``make verify``, so the floor holds with no
  toolchain beyond Python.
* **Contrast.** WCAG 2.2 contrast is arithmetic over two palettes, and both
  palettes are data in :mod:`gauntlet.site`. Every pair the stylesheet actually
  puts together is measured here, in both themes. This is the one WCAG criterion
  a headless check settles completely and axe cannot: jsdom paints nothing.
* **Claims.** No page may claim state approval, endorsement, or compliance, and
  no page may imply a published package. The same rules the prose files live
  under, applied to what is published.
* **Counts.** The gate table on the site must equal what the harness loads, and
  the evidence excerpts must be real output from a run made while the site was
  built. A page that can print a number nothing counted is the failure this
  project exists to avoid.

What is deliberately not claimed: none of this looks at the pages. Layout,
reflow at small widths, focus visibility in practice, and reading order under a
real screen reader need a person.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from html import escape
from html.parser import HTMLParser
from pathlib import Path

import pytest

from gauntlet.cases import builtin_suites
from gauntlet.cli import main
from gauntlet.evidence import (
    ALIGNMENT_NOTICE,
    CLEAN_RUN_CAVEAT,
    NOT_ESTABLISHED,
    build_evidence_pack,
)
from gauntlet.inventory import build_inventory
from gauntlet.mapping import UNVERIFIED_IDENTIFIERS
from gauntlet.report import render_markdown
from gauntlet.results import run_summary_lines
from gauntlet.site import (
    DARK,
    LIGHT,
    PAGES,
    STYLESHEET,
    ActionMetadataError,
    Column,
    Table,
    broken_run,
    build_site,
    healthy_run,
    load_action,
    markdown_section,
    render_site,
    render_table,
)

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / "action.yml"
PAGE_NAMES = tuple(name for name, _label, _key in PAGES)
HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("site")
    build_site(out, action_file=ACTION)
    return out


# ---------------------------------------------------------------------------
# A parser that records the document facts these checks are about
# ---------------------------------------------------------------------------


@dataclass
class TableFacts:
    """What a data table must carry: a name, and a header for every cell."""

    caption: bool = False
    headers: int = 0
    scoped: int = 0


class Document(HTMLParser):
    """Structural facts, gathered in one pass over the markup."""

    PROSE_TAGS = frozenset({"p", "li", "dd", "dt", "caption", *HEADINGS})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.headings: list[tuple[str, str]] = []
        self.ids: list[str] = []
        self.landmarks: Counter[str] = Counter()
        self.tables: list[TableFacts] = []
        self.th_scopes: list[str | None] = []
        self.hrefs: list[str] = []
        self.scripts = 0
        self.inline_styles = 0
        self.lang: str | None = None
        self.title = ""
        self.metas: dict[str, str] = {}
        self.prose: list[str] = []
        self.cells: list[str] = []
        self._heading: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key: (value or "") for key, value in attrs}
        self.stack.append(tag)
        if "id" in attr:
            self.ids.append(attr["id"])
        if "style" in attr:
            self.inline_styles += 1
        if tag == "a" and "href" in attr:
            self.hrefs.append(attr["href"])
        if tag in HEADINGS:
            self._heading = tag
            self._text = []
        elif tag in ("nav", "main", "footer", "header"):
            self.landmarks[tag] += 1
        elif tag == "script":
            self.scripts += 1
        else:
            self._note_head(tag, attr)
            self._note_table(tag, attr)

    def _note_head(self, tag: str, attr: dict[str, str]) -> None:
        if tag == "html":
            self.lang = attr.get("lang")
        elif tag == "meta":
            key = attr.get("name") or ("charset" if "charset" in attr else "")
            if key:
                self.metas[key] = attr.get("content", attr.get("charset", ""))

    def _note_table(self, tag: str, attr: dict[str, str]) -> None:
        if tag == "table":
            self.tables.append(TableFacts())
        elif tag == "th" and self.tables:
            self.tables[-1].headers += 1
            self.th_scopes.append(attr.get("scope"))
            if attr.get("scope") in ("row", "col"):
                self.tables[-1].scoped += 1
        elif tag == "caption" and self.tables:
            self.tables[-1].caption = True

    def handle_endtag(self, tag: str) -> None:
        if tag in HEADINGS and self._heading == tag:
            self.headings.append((tag, "".join(self._text).strip()))
            self._heading = None
        while self.stack:
            if self.stack.pop() == tag:
                break

    def handle_data(self, data: str) -> None:
        if self._heading is not None:
            self._text.append(data)
        if "style" in self.stack:
            return
        if "title" in self.stack:
            self.title += data
            return
        if self.stack and self.stack[-1] == "td":
            self.cells.append(data)
        if any(tag in self.PROSE_TAGS for tag in self.stack):
            self.prose.append(data)


def parse(path: Path) -> Document:
    doc = Document()
    doc.feed(path.read_text(encoding="utf-8"))
    return doc


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


def test_the_build_writes_every_declared_page(built: Path) -> None:
    written = sorted(path.name for path in built.glob("*.html"))
    assert written == sorted(PAGE_NAMES)


@pytest.mark.parametrize("name", PAGE_NAMES)
def test_the_page_declares_a_language_charset_and_viewport(built: Path, name: str) -> None:
    doc = parse(built / name)
    assert doc.lang == "en"
    assert doc.metas.get("charset") == "utf-8"
    assert "width=device-width" in doc.metas.get("viewport", "")


@pytest.mark.parametrize("name", PAGE_NAMES)
def test_the_page_has_a_title_and_a_description(built: Path, name: str) -> None:
    doc = parse(built / name)
    assert doc.title.strip()
    assert doc.metas.get("description", "").strip()


@pytest.mark.parametrize("name", PAGE_NAMES)
def test_there_is_exactly_one_first_level_heading(built: Path, name: str) -> None:
    levels = [tag for tag, _ in parse(built / name).headings]
    assert levels.count("h1") == 1, levels


@pytest.mark.parametrize("name", PAGE_NAMES)
def test_no_heading_level_is_skipped(built: Path, name: str) -> None:
    """A jump from h1 to h3 tells a screen-reader user a section is missing."""
    previous = 0
    for tag, text in parse(built / name).headings:
        level = int(tag[1])
        assert level <= previous + 1, f"{name}: {tag} {text!r} follows h{previous}"
        previous = level


@pytest.mark.parametrize("name", PAGE_NAMES)
def test_every_heading_has_text(built: Path, name: str) -> None:
    for tag, text in parse(built / name).headings:
        assert text, f"{name}: empty {tag}"


@pytest.mark.parametrize("name", PAGE_NAMES)
def test_no_id_is_used_twice(built: Path, name: str) -> None:
    repeated = [value for value, count in Counter(parse(built / name).ids).items() if count > 1]
    assert not repeated, f"{name}: {repeated}"


@pytest.mark.parametrize("name", PAGE_NAMES)
def test_the_page_has_the_landmarks_a_reader_navigates_by(built: Path, name: str) -> None:
    landmarks = parse(built / name).landmarks
    assert landmarks["main"] == 1
    assert landmarks["nav"] == 1
    assert landmarks["footer"] >= 1


@pytest.mark.parametrize("name", PAGE_NAMES)
def test_the_skip_link_points_at_something_on_the_page(built: Path, name: str) -> None:
    doc = parse(built / name)
    fragments = [href[1:] for href in doc.hrefs if href.startswith("#")]
    assert fragments, f"{name}: no in-page link"
    for target in fragments:
        assert target in doc.ids, f"{name}: #{target} has no target"


@pytest.mark.parametrize("name", PAGE_NAMES)
def test_every_internal_link_resolves(built: Path, name: str) -> None:
    for href in parse(built / name).hrefs:
        if href.startswith(("#", "http://", "https://", "mailto:")):
            continue
        assert (built / href).exists(), f"{name}: {href} does not exist"


@pytest.mark.parametrize("name", PAGE_NAMES)
def test_every_table_is_named_and_every_header_scoped(built: Path, name: str) -> None:
    doc = parse(built / name)
    for index, table in enumerate(doc.tables):
        assert table.caption, f"{name}: table {index} has no caption"
        assert table.headers, f"{name}: table {index} has no header cells"
        assert table.scoped == table.headers, f"{name}: table {index} has a bare th"
    if doc.tables:
        # A column header alone leaves a cell unidentified in one of its two
        # directions, so every table is headed both ways.
        scopes = Counter(doc.th_scopes)
        assert scopes["row"] > 0 and scopes["col"] > 0


def test_the_pages_that_carry_tables_carry_them(built: Path) -> None:
    """Guard the check above against a build that emits no table at all."""
    with_tables = {name for name in PAGE_NAMES if parse(built / name).tables}
    assert with_tables == {"gates.html", "california.html", "action.html"}


@pytest.mark.parametrize("name", PAGE_NAMES)
def test_the_page_ships_no_script_and_no_inline_style(built: Path, name: str) -> None:
    """Static pages, no runtime, nothing for a CSP to have to allow."""
    doc = parse(built / name)
    assert doc.scripts == 0
    assert doc.inline_styles == 0


@pytest.mark.parametrize("name", PAGE_NAMES)
def test_the_page_carries_no_em_dash_or_en_dash(built: Path, name: str) -> None:
    text = (built / name).read_text(encoding="utf-8")
    assert chr(0x2014) not in text
    assert chr(0x2013) not in text


# ---------------------------------------------------------------------------
# Claims
# ---------------------------------------------------------------------------

FORBIDDEN_CLAIMS = (
    "approved by the state",
    "endorsed by the state of california.",
    "certified by cdt",
    "california-compliant",
    "simm 5305-f compliant",
    "state-approved",
    "officially recognized",
)

PACKAGE_BADGES = ("img.shields.io", "pypi.org/project", "badge.fury.io", "pypi version")


@pytest.mark.parametrize("name", PAGE_NAMES)
def test_no_page_claims_state_approval_or_endorsement(built: Path, name: str) -> None:
    text = (built / name).read_text(encoding="utf-8").casefold()
    for claim in FORBIDDEN_CLAIMS:
        assert claim not in text, f"{name} claims {claim!r}"


@pytest.mark.parametrize("name", PAGE_NAMES)
def test_no_page_implies_a_published_package(built: Path, name: str) -> None:
    text = (built / name).read_text(encoding="utf-8").casefold()
    for badge in PACKAGE_BADGES:
        assert badge not in text, f"{name} implies a published package via {badge!r}"
    # PyPI may be named, but only by the notice that says what is on it: the
    # harness, as gauntlet-evals, and never the action.
    assert text.count("pypi") == text.count("published to pypi as gauntlet-evals")


@pytest.mark.parametrize("name", PAGE_NAMES)
def test_every_page_carries_the_alignment_notice(built: Path, name: str) -> None:
    """The notice is the harness's own constant, so it cannot drift from the pack."""
    text = (built / name).read_text(encoding="utf-8")
    assert ALIGNMENT_NOTICE.split(". ")[0] in text
    assert "have not reviewed, approved, endorsed, or certified" in text


def test_the_front_page_says_what_it_is_not_before_the_quickstart() -> None:
    """The framing is load-bearing. Buried, it is not doing its job."""
    page = render_site(load_action(ACTION))["index.html"]
    not_a_certification = page.index("Not a compliance certification")
    assert not_a_certification < page.index("Quickstart")
    assert not_a_certification < page.index("git clone")
    for phrase in ("Not a model benchmark", "Not a red-team service"):
        assert phrase in page


def test_the_california_page_leads_with_the_alignment_notice() -> None:
    page = render_site(load_action(ACTION))["california.html"]
    body = page.split('<main id="content">')[1]
    assert body.index("Aligned to, not approved or endorsed by") < body.index("What it is for")


def test_the_california_page_omits_every_unverified_identifier_from_the_mapping() -> None:
    """The omitted list stays omitted: named as omitted, never cited as support."""
    page = render_site(load_action(ACTION))["california.html"]
    mapping_table = page.split('id="cap-mapping"')[1].split("</table>")[0]
    for item in UNVERIFIED_IDENTIFIERS:
        head = item.identifier.split(":")[0].split(",")[0].strip()
        assert head not in mapping_table, f"{head} is cited in the mapping table"
        assert item.identifier in page, f"{item.identifier} is not listed as omitted"


# ---------------------------------------------------------------------------
# Counts: counted by the harness, never typed
# ---------------------------------------------------------------------------


def test_the_gate_table_matches_what_the_harness_loads(built: Path) -> None:
    """The site's inventory is rendered from the same function make inventory uses."""
    inventory = build_inventory(builtin_suites())
    text = (built / "gates.html").read_text(encoding="utf-8")
    table = text.split('id="cap-inventory"')[1].split("</table>")[0]
    for gate in inventory.gates:
        cells = "".join(
            f'<td class="num">{gate.counts_by_language[code]}</td>' for code in inventory.languages
        )
        row = (
            f'<th scope="row"><code>{gate.gate}</code></th>'
            f"<td><code>{gate.suite}</code></td>"
            f'<td class="num">{gate.threshold * 100:g}%</td>'
            f'{cells}<td class="num">{gate.total}</td>'
        )
        assert row in table, f"the site's row for {gate.gate} is not the harness's row"
    assert f"{len(inventory.gates)} gates, {inventory.total_cases} cases." in text


def test_the_gate_table_moves_when_a_case_is_added() -> None:
    """The counts are computed, not copied. Prove it by changing the input."""
    inventory = build_inventory(builtin_suites())
    page = render_site(load_action(ACTION))["gates.html"]
    assert f'<td class="num">{inventory.total_cases}</td>' in page
    assert f'<td class="num">{inventory.total_cases + 1}</td>' not in page


def test_the_evidence_excerpts_are_real_output(built: Path) -> None:
    """Rebuild the same runs here and require the page to carry them in full.

    Not a substring of an excerpt, and not a paraphrase: the whole section, as
    the reporter rendered it, escaped for HTML and nothing else.
    """
    healthy = healthy_run()
    broken = broken_run()
    clean_document = render_markdown(build_evidence_pack(healthy.to_dict()))
    broken_document = render_markdown(build_evidence_pack(broken.to_dict(), healthy.to_dict()))
    text = (built / "evidence.html").read_text(encoding="utf-8")
    for document, heading in (
        (clean_document, "## What failed"),
        (broken_document, "## What failed"),
        (broken_document, "## Run-to-run drift"),
    ):
        excerpt = escape(markdown_section(document, heading), quote=True)
        assert excerpt in text, f"the page does not carry the {heading!r} section verbatim"
    assert "uncited answer" in text, "the failing excerpt does not show why a case failed"


def test_the_evidence_page_carries_the_clean_run_caveat(built: Path) -> None:
    text = (built / "evidence.html").read_text(encoding="utf-8")
    assert CLEAN_RUN_CAVEAT in text
    for item in NOT_ESTABLISHED:
        assert item.replace("'", "&#x27;") in text or item in text


def test_the_healthy_run_the_site_shows_is_a_passing_run() -> None:
    run = healthy_run()
    assert run.passed
    assert run.gates


def test_the_broken_run_the_site_shows_actually_fails() -> None:
    run = broken_run()
    assert not run.passed
    assert any(not gate.passed for gate in run.gates)


def test_the_overview_prints_the_command_output_it_says_it_prints(built: Path) -> None:
    """The quickstart's sample output is generated, not transcribed."""
    text = (built / "index.html").read_text(encoding="utf-8")
    for line in run_summary_lines(healthy_run()):
        assert line.strip() in text


# A whole number standing on its own in a sentence. Identifiers keep their shape and
# are not matched: "SIMM 5305-F", "SAM 4986.9", "Apache-2.0" and "sha256" all have a
# letter, a hyphen or a dot against the digits.
STANDALONE_NUMBER = re.compile(r"(?<![\w.\-/])\d+(?![\w.\-/])")

# Standalone numbers that no run produced. Each is here because it was checked, and a
# new one has to be added deliberately rather than slipping in.
REVIEWED_NUMBERS: dict[str, str] = {
    "1": "the exit code gauntlet run uses for a failed gate",
    "2": "the exit code gauntlet run uses when the harness itself could not run",
    "4": "the exit code gauntlet run uses when the run cannot be scored",
    "4986": "the SAM 4986 series, named while explaining the correction made by reading",
}


@pytest.mark.parametrize("name", PAGE_NAMES)
def test_every_number_in_prose_was_counted_or_reviewed(built: Path, name: str) -> None:
    """A page that can print a figure nothing counted is what this project avoids."""
    inventory = build_inventory(builtin_suites())
    counted = {str(inventory.total_cases), str(len(inventory.gates))}
    counted |= {str(value) for value in inventory.totals_by_language.values()}
    counted |= {str(gate.total) for gate in inventory.gates}
    counted |= {
        str(count) for gate in inventory.gates for count in gate.counts_by_language.values()
    }
    for chunk in parse(built / name).prose:
        for match in STANDALONE_NUMBER.findall(chunk):
            assert match in counted or match in REVIEWED_NUMBERS, (
                f"{name}: {match!r} in prose is neither counted nor on the reviewed list"
            )


def test_the_reviewed_number_list_has_no_stale_entries(built: Path) -> None:
    """A reviewed number nothing prints any more is a rule nobody is following."""
    printed = {
        match
        for name in PAGE_NAMES
        for chunk in parse(built / name).prose
        for match in STANDALONE_NUMBER.findall(chunk)
    }
    assert set(REVIEWED_NUMBERS) <= printed, set(REVIEWED_NUMBERS) - printed


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_the_build_is_byte_identical_twice(tmp_path: Path) -> None:
    first = render_site(load_action(ACTION))
    second = render_site(load_action(ACTION))
    assert first == second
    out = tmp_path / "site"
    build_site(out, action_file=ACTION)
    written = {path.name: path.read_text(encoding="utf-8") for path in out.glob("*.html")}
    assert written == first


def test_a_generated_date_appears_only_when_one_is_given(tmp_path: Path) -> None:
    without = render_site(load_action(ACTION))["index.html"]
    assert "Built on" not in without
    with_date = render_site(load_action(ACTION), generated="2026-08-07")["index.html"]
    assert "Built on 2026-08-07" in with_date


# ---------------------------------------------------------------------------
# Contrast. WCAG 2.2: 1.4.3 for text, 1.4.11 for the non-text boundaries.
# ---------------------------------------------------------------------------


def relative_luminance(colour: str) -> float:
    """WCAG relative luminance of an sRGB hex colour."""
    raw = colour.lstrip("#")
    channels = [int(raw[index : index + 2], 16) / 255 for index in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(first: str, second: str) -> float:
    """WCAG contrast ratio, at least 1.0 whichever way round the pair is given."""
    lighter, darker = sorted((relative_luminance(first), relative_luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


TEXT_PAIRS: tuple[tuple[str, str, str], ...] = (
    ("ink", "surface", "body copy on the page background"),
    ("ink", "surface-raised", "table body text and the nav bar"),
    ("ink", "notice-bg", "the notice text"),
    ("ink", "code-bg", "code samples and inline code"),
    ("ink-2", "surface", "the standfirst and the footer"),
    ("ink-2", "surface-raised", "definition-list descriptions in a card"),
    ("ink-2", "notice-bg", "a second paragraph inside a notice"),
    ("ink-2", "code-bg", "inline code inside secondary text"),
    ("ink-3", "surface", "the eyebrow above each h1"),
    ("ink-3", "surface-raised", "column headers and table captions"),
    ("accent", "surface", "links in body copy"),
    ("accent", "surface-raised", "links in the nav bar and in cards"),
    ("accent", "notice-bg", "the link inside the alignment notice"),
)
"""Every foreground and background the stylesheet puts together. All of this text is
under 24px and none of it is bold at 19px or more, so 4.5:1 applies throughout."""

BOUNDARY_PAIRS: tuple[tuple[str, str, str], ...] = (
    ("rule-strong", "surface", "the notice border against the page"),
    ("rule-strong", "notice-bg", "the notice border against its own fill"),
    ("rule-strong", "surface-raised", "the skip link's border when it is focused"),
    ("accent", "surface", "the focus outline on a link"),
    ("accent", "surface-raised", "the focus outline inside the nav bar"),
)
"""Boundaries that carry meaning or mark a control, held to 1.4.11's 3:1."""

TEXT_MINIMUM = 4.5
BOUNDARY_MINIMUM = 3.0
PALETTES = {"light": LIGHT, "dark": DARK}


@pytest.mark.parametrize("theme", sorted(PALETTES))
@pytest.mark.parametrize("pair", TEXT_PAIRS, ids=lambda pair: f"{pair[0]}-on-{pair[1]}")
def test_text_meets_wcag_contrast(theme: str, pair: tuple[str, str, str]) -> None:
    foreground, background, where = pair
    ratio = contrast(PALETTES[theme][foreground], PALETTES[theme][background])
    assert ratio >= TEXT_MINIMUM, f"{theme}: {where} is {ratio:.2f}:1, below {TEXT_MINIMUM}:1"


@pytest.mark.parametrize("theme", sorted(PALETTES))
@pytest.mark.parametrize("pair", BOUNDARY_PAIRS, ids=lambda pair: f"{pair[0]}-on-{pair[1]}")
def test_boundaries_meet_wcag_non_text_contrast(theme: str, pair: tuple[str, str, str]) -> None:
    foreground, background, where = pair
    ratio = contrast(PALETTES[theme][foreground], PALETTES[theme][background])
    assert ratio >= BOUNDARY_MINIMUM, (
        f"{theme}: {where} is {ratio:.2f}:1, below {BOUNDARY_MINIMUM}:1"
    )


def test_both_palettes_define_exactly_the_same_tokens() -> None:
    """A token defined in one theme only leaves a colour inherited from the host."""
    assert set(LIGHT) == set(DARK)


def test_every_token_is_used_by_the_stylesheet() -> None:
    for token in LIGHT:
        assert f"var(--{token})" in STYLESHEET, f"--{token} is defined but never used"


def test_the_stylesheet_defines_both_themes_and_honours_an_explicit_choice() -> None:
    assert "@media (prefers-color-scheme: dark)" in STYLESHEET
    assert ':root:not([data-theme="light"])' in STYLESHEET
    assert ':root[data-theme="dark"]' in STYLESHEET
    assert "background: var(--surface)" in STYLESHEET


def test_a_known_contrast_is_computed_correctly() -> None:
    """Anchor the arithmetic: black on white is 21:1, and a colour on itself is 1:1."""
    assert round(contrast("#000000", "#ffffff"), 2) == 21.0
    assert round(contrast("#14509c", "#14509c"), 2) == 1.0


# ---------------------------------------------------------------------------
# The action metadata the site reads, and the CLI entry point
# ---------------------------------------------------------------------------


def test_the_action_tables_are_read_from_the_action_definition() -> None:
    action = load_action(ACTION)
    names = {field.name for field in action.inputs}
    assert {"cases", "target-url", "target-callable", "baseline"} <= names
    outputs = {field.name for field in action.outputs}
    assert {"passed", "gates-failed", "cases-total", "results-digest"} <= outputs
    page = render_site(action)["action.html"]
    for field in action.inputs:
        assert f"<code>{field.name}</code>" in page
    for field in action.outputs:
        assert f"<code>{field.name}</code>" in page


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ("[]", "top level must be a mapping"),
        ("inputs: {}\noutputs: {a: {description: x}}", "'inputs' is missing or empty"),
        ("inputs: {a: {description: x}}\noutputs: {}", "'outputs' is missing or empty"),
        ("inputs: {a: []}\noutputs: {b: {description: x}}", "is not a mapping"),
        ("inputs: {a: {description: '  '}}\noutputs: {b: {description: x}}", "no description"),
    ],
)
def test_a_malformed_action_definition_is_rejected(
    tmp_path: Path, document: str, message: str
) -> None:
    path = tmp_path / "action.yml"
    path.write_text(document, encoding="utf-8")
    with pytest.raises(ActionMetadataError, match=re.escape(message)):
        load_action(path)


def test_an_action_default_of_none_reads_as_empty(tmp_path: Path) -> None:
    path = tmp_path / "action.yml"
    path.write_text(
        "inputs:\n  a:\n    description: does a thing\n    default: null\n"
        "outputs:\n  b:\n    description: reports a thing\n",
        encoding="utf-8",
    )
    assert load_action(path).inputs[0].default == ""


def test_markdown_section_refuses_a_heading_that_is_not_there() -> None:
    with pytest.raises(ValueError, match="no section"):
        markdown_section("# doc\n\n## Present\n\ntext\n", "## Absent")


def test_markdown_section_stops_at_the_next_section() -> None:
    document = "## One\n\nfirst\n\n## Two\n\nsecond\n"
    assert markdown_section(document, "## One") == "## One\n\nfirst"
    assert markdown_section(document, "## Two") == "## Two\n\nsecond"


def test_a_table_row_of_the_wrong_width_is_a_build_error() -> None:
    table = Table(
        slug="broken",
        caption="A table whose row does not match its columns.",
        columns=(Column("One"), Column("Two")),
        rows=(("only",),),
    )
    with pytest.raises(ValueError, match="argument 2 is shorter"):
        render_table(table)


def test_the_cli_writes_the_site(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "site"
    code = main(["site", "--out", str(out), "--action-file", str(ACTION)])
    assert code == 0
    assert (out / "index.html").exists()
    assert "wrote" in capsys.readouterr().out


def test_the_cli_reports_a_missing_action_definition(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        ["site", "--out", str(tmp_path / "site"), "--action-file", str(tmp_path / "nope.yml")]
    )
    assert code == 2
    assert "error:" in capsys.readouterr().err
