"""A suite declares the languages it covers, and every count derives from that.

The rules under test, and why each one exists:

* A suite that writes nothing behaves exactly as it did before this existed.
  That is the constraint the whole change is shaped around, and it is asserted
  first, on the built-ins and on the generated README block.
* A declared language with no cases and no exception is refused at load. A
  declaration is a claim; a run must not reach a verdict over a claim it never
  exercised.
* An exception needs a reason, cannot name an undeclared language, and cannot
  name a covered one. A stale exception is a check switched off where nobody
  looking at the suite can see it.
* Right-to-left content survives the round trip: the legibility predicate reads
  Arabic letters and Arabic-Indic digits, invisible bidi controls are rejected
  in identifiers, and RTL cells are isolated so a table's columns render in the
  order the file has them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gauntlet.bidi import (
    FIRST_STRONG_ISOLATE,
    POP_DIRECTIONAL_ISOLATE,
    bidi_controls_in,
    has_strong_rtl,
    isolate,
)
from gauntlet.cases import (
    DEFAULT_LANGUAGES,
    CaseFileError,
    all_languages,
    builtin_suites,
    iter_case_counts,
    load_suite_text,
)
from gauntlet.gates.readability import is_readable
from gauntlet.inventory import build_inventory, coverage_sentence, render_inventory_markdown
from gauntlet.lint import ERROR, WARNING, lint_directory
from gauntlet.report import _cell

# Arabic case prompts, kept to two unambiguous forms: "What are the library
# hours?" and a greeting. They are fixtures for the machinery, not a shipped
# Arabic suite; publishing Arabic cases needs a reviewer who reads Arabic.
_ARABIC_PROMPT = "ما هي ساعات المكتبة؟"
_ARABIC_GREETING = "مرحبا"

THREE_LANGUAGE_SUITE = f"""
suite: three-language-grounding
gate: grounding
version: 1
languages: [en, es, ar]
cases:
  - id: gnd-en-library
    language: en
    prompt: What are the library hours?
    expect_grounded: true
    must_contain: ["library"]
  - id: gnd-es-biblioteca
    language: es
    prompt: ¿Cuál es el horario de la biblioteca?
    expect_grounded: true
    must_contain: ["biblioteca"]
  - id: gnd-ar-library
    language: ar
    prompt: {_ARABIC_PROMPT}
    expect_grounded: true
    must_contain: ["المكتبة"]
"""

_MINIMAL = """
suite: t
gate: grounding
version: 1
{header}cases:
  - id: a
    language: en
    prompt: hello
    expect_grounded: false
    must_contain: []
{extra}"""


def _suite(header: str = "", extra: str = "") -> str:
    return _MINIMAL.format(header=header, extra=extra)


_ES_CASE = """  - id: b
    language: es
    prompt: hola
    expect_grounded: false
    must_contain: []
"""


# --- a suite that declares nothing is unchanged ------------------------------


def test_a_suite_that_declares_nothing_gets_the_default_pair() -> None:
    suite = load_suite_text(_suite(), "t")
    assert suite.languages == DEFAULT_LANGUAGES
    assert suite.languages_declared is False
    assert suite.coverage_exceptions == ()


def test_an_undeclared_suite_missing_a_language_still_loads() -> None:
    """The load-time coverage rule must not fire on a suite that claimed nothing.

    An English-only third-party suite loads and runs today, and ``gauntlet
    lint`` is where it is told off. Moving that to the loader would break
    working setups on upgrade, which is the one thing this change promised not
    to do.
    """
    suite = load_suite_text(_suite(), "t")
    assert {case.language for case in suite.cases} == {"en"}


def test_the_builtins_declare_nothing_and_count_the_same() -> None:
    suites = builtin_suites()
    assert all(suite.languages_declared is False for suite in suites)
    assert all_languages(suites) == DEFAULT_LANGUAGES
    adversarial = {(lang, n) for gate, lang, n in iter_case_counts(suites) if gate == "adversarial"}
    assert adversarial == {("en", 12), ("es", 12)}


def test_the_generated_readme_block_is_unchanged_for_the_builtins() -> None:
    """The committed block is the assertion: rendering it must still match."""
    block = render_inventory_markdown(build_inventory(builtin_suites()))
    committed = Path("README.md").read_text(encoding="utf-8")
    assert block in committed


# --- declaring languages -----------------------------------------------------


def test_a_third_language_loads_and_is_counted() -> None:
    suite = load_suite_text(THREE_LANGUAGE_SUITE, "three.yaml")
    assert suite.languages == ("en", "es", "ar")
    assert suite.languages_declared is True
    inventory = build_inventory((suite,))
    assert inventory.languages == ("ar", "en", "es")
    assert inventory.gates[0].counts_by_language == {"ar": 1, "en": 1, "es": 1}


def test_the_inventory_table_shows_the_third_language_as_a_column() -> None:
    markdown = render_inventory_markdown(
        build_inventory((load_suite_text(THREE_LANGUAGE_SUITE, "s"),))
    )
    header = markdown.splitlines()[0]
    assert "| ar |" in header
    assert "English" in header and "Spanish" in header


def test_a_case_in_an_undeclared_language_is_refused_with_the_declared_set() -> None:
    text = THREE_LANGUAGE_SUITE.replace("language: ar", "language: fr")
    with pytest.raises(CaseFileError) as caught:
        load_suite_text(text, "three.yaml")
    assert "'language' must be one of ['en', 'es', 'ar']" in str(caught.value)
    assert "three.yaml" in str(caught.value)


@pytest.mark.parametrize("tag", ["EN", "english", "es_MX", "e", "abcd", "en-us", "zh-hans"])
def test_a_malformed_language_tag_is_refused(tag: str) -> None:
    with pytest.raises(CaseFileError, match="not a well-formed BCP-47 tag"):
        load_suite_text(_suite(header=f"languages: [en, {tag}]\n"), "t")


@pytest.mark.parametrize("tag", ["ar", "vi", "pt-BR", "zh-Hans", "zh-Hans-CN", "es-419", "haw"])
def test_a_well_formed_tag_is_accepted(tag: str) -> None:
    text = _suite(
        header=f"languages: [en, {tag}]\n",
        extra=f"""  - id: b
    language: {tag}
    prompt: hola
    expect_grounded: false
    must_contain: []
""",
    )
    assert load_suite_text(text, "t").languages == ("en", tag)


def test_languages_must_be_a_non_empty_list() -> None:
    with pytest.raises(CaseFileError, match="'languages' must be a non-empty list"):
        load_suite_text(_suite(header="languages: []\n"), "t")


def test_a_repeated_language_is_refused() -> None:
    with pytest.raises(CaseFileError, match=r"'languages' repeats \['en'\]"):
        load_suite_text(_suite(header="languages: [en, en]\n"), "t")


# --- the coverage rule -------------------------------------------------------


def test_a_declared_language_with_no_cases_is_refused_at_load() -> None:
    with pytest.raises(CaseFileError) as caught:
        load_suite_text(_suite(header="languages: [en, es, ar]\n"), "t")
    message = str(caught.value)
    assert "declares ['en', 'es', 'ar']" in message
    assert "has no cases in ['ar', 'es']" in message


def test_an_exception_lets_a_declared_language_have_no_cases() -> None:
    suite = load_suite_text(
        _suite(
            header=(
                "languages: [en, es, ar]\n"
                "coverage_exceptions:\n"
                "  - language: ar\n"
                "    reason: no Arabic reviewer has read these prompts yet\n"
            ),
            extra=_ES_CASE,
        ),
        "t",
    )
    assert suite.excepted_languages() == {"ar"}
    inventory = build_inventory((suite,))
    assert inventory.gates[0].counts_by_language["ar"] == 0
    assert inventory.declared_but_not_covered == (
        ("grounding", "ar", "no Arabic reviewer has read these prompts yet"),
    )
    payload = inventory.gates[0].to_dict()
    assert payload["declared_languages"] == ["en", "es", "ar"]
    assert payload["coverage_exceptions"] == [
        {"language": "ar", "reason": "no Arabic reviewer has read these prompts yet"}
    ]


def test_the_coverage_sentence_states_a_claimed_but_uncovered_language() -> None:
    """A zero column is ambiguous; the sentence beside the table is what resolves it."""
    suite = load_suite_text(
        _suite(
            header=(
                "languages: [en, es, ar]\n"
                "coverage_exceptions:\n"
                "  - language: ar\n"
                "    reason: no Arabic reviewer yet\n"
            ),
            extra=_ES_CASE,
        ),
        "t",
    )
    sentence = coverage_sentence(build_inventory((suite,)))
    assert "declares `ar` and covers none of it" in sentence
    assert "no Arabic reviewer yet" in sentence


def test_a_non_string_language_is_refused() -> None:
    with pytest.raises(CaseFileError, match="each language must be a non-empty string"):
        load_suite_text(_suite(header="languages: [en, 42]\n"), "t")


def test_an_exception_for_a_covered_language_is_refused() -> None:
    text = THREE_LANGUAGE_SUITE + ("coverage_exceptions:\n  - language: ar\n    reason: stale\n")
    with pytest.raises(CaseFileError, match="excepts 'ar' from coverage but has cases in it"):
        load_suite_text(text, "t")


def test_an_exception_for_an_undeclared_language_is_refused() -> None:
    text = _suite(
        header=("languages: [en, es]\ncoverage_exceptions:\n  - language: ar\n    reason: nope\n"),
        extra=_ES_CASE,
    )
    with pytest.raises(CaseFileError, match="'ar' is not declared by this suite"):
        load_suite_text(text, "t")


def test_an_exception_without_a_declaration_is_refused() -> None:
    text = _suite(header="coverage_exceptions:\n  - language: es\n    reason: nope\n")
    with pytest.raises(CaseFileError, match="needs a 'languages' declaration"):
        load_suite_text(text, "t")


def test_an_exception_needs_a_reason() -> None:
    text = _suite(
        header="languages: [en, es]\ncoverage_exceptions:\n  - language: es\n",
        extra="",
    )
    with pytest.raises(CaseFileError, match="'reason' must be a non-empty string"):
        load_suite_text(text, "t")


@pytest.mark.parametrize(
    ("block", "fragment"),
    [
        ("coverage_exceptions: []\n", "must be a non-empty list"),
        ("coverage_exceptions:\n  - es\n", "each exception must be a mapping"),
        ("coverage_exceptions:\n  - language: es\n    reason: r\n    why: x\n", "unknown keys"),
        (
            "coverage_exceptions:\n  - language: es\n    reason: a\n  - language: es\n    reason: b\n",
            "more than once",
        ),
    ],
)
def test_malformed_coverage_exceptions_are_refused(block: str, fragment: str) -> None:
    with pytest.raises(CaseFileError, match=fragment):
        load_suite_text(_suite(header="languages: [en, es]\n" + block), "t")


# --- lint ---------------------------------------------------------------------


def _write(directory: Path, name: str, text: str) -> None:
    (directory / name).write_text(text, encoding="utf-8")


def test_lint_locates_a_declared_language_with_no_cases(tmp_path: Path) -> None:
    """A declared gap fails the loader, so lint reports it as the located schema error.

    Lint's own ``missing_language`` check is what remains for a suite that
    declares nothing: its default pair is not a claim the loader enforces, so
    the peer rule for it stays exactly where it was.
    """
    _write(
        tmp_path,
        "grounding.yaml",
        THREE_LANGUAGE_SUITE.replace("    language: ar", "    language: en").replace(
            "gnd-ar-library", "gnd-en-library-2"
        ),
    )
    report = lint_directory(tmp_path)
    finding = next(item for item in report.findings if item.code == "schema")
    assert finding.severity == ERROR
    assert "has no cases in ['ar']" in finding.message
    assert not report.ok


def test_lint_still_warns_on_imbalance_across_three_languages(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "grounding.yaml",
        THREE_LANGUAGE_SUITE
        + """  - id: gnd-en-extra
    language: en
    prompt: And the pool hours?
    expect_grounded: true
    must_contain: ["pool"]
""",
    )
    report = lint_directory(tmp_path)
    finding = next(item for item in report.findings if item.code == "language_imbalance")
    assert finding.severity == WARNING
    assert "1 ar, 2 en, 1 es" in finding.message
    assert report.ok


def test_lint_reports_every_exception_it_honours(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "grounding.yaml",
        _suite(
            header=(
                "languages: [en, es, ar]\n"
                "coverage_exceptions:\n"
                "  - language: ar\n"
                "    reason: no Arabic reviewer yet\n"
            ),
            extra=_ES_CASE,
        ),
    )
    report = lint_directory(tmp_path)
    honoured = [item for item in report.findings if item.code == "language_excepted"]
    assert [item.severity for item in honoured] == [WARNING]
    assert "no Arabic reviewer yet" in honoured[0].message
    assert not [item for item in report.findings if item.code == "missing_language"]


# --- right-to-left -------------------------------------------------------------


@pytest.mark.parametrize("text", [_ARABIC_PROMPT, _ARABIC_GREETING, "٢٠٢٦"])
def test_the_legibility_predicate_reads_arabic(text: str) -> None:
    """The claim that the predicate already survives non-Latin scripts, measured."""
    assert is_readable(text)


@pytest.mark.parametrize("text", ["‏‏", "؜", "⁦⁩"])
def test_bidi_controls_alone_are_not_a_readable_answer(text: str) -> None:
    assert not is_readable(text)


def test_a_bidi_control_in_a_case_id_is_refused() -> None:
    text = THREE_LANGUAGE_SUITE.replace("gnd-ar-library", "gnd-ar-‮library")
    with pytest.raises(CaseFileError) as caught:
        load_suite_text(text, "t")
    assert "U+202E" in str(caught.value)
    assert "invisible" in str(caught.value)


def test_a_bidi_control_in_a_language_tag_is_refused() -> None:
    with pytest.raises(CaseFileError, match=r"U\+200F"):
        load_suite_text(_suite(header="languages: [en, ‏ar]\n"), "t")


def test_ascii_cells_are_returned_byte_identical() -> None:
    """The property that makes isolating every cell safe for every existing pack."""
    for value in ["gnd-en-library", "PASS", "0.983", "", "a | b"]:
        assert isolate(value) == value


def test_an_rtl_cell_is_isolated() -> None:
    rendered = _cell(_ARABIC_PROMPT)
    assert rendered.startswith(FIRST_STRONG_ISOLATE)
    assert rendered.endswith(POP_DIRECTIONAL_ISOLATE)
    assert _ARABIC_PROMPT in rendered


def test_has_strong_rtl_and_control_naming() -> None:
    assert has_strong_rtl(_ARABIC_GREETING)
    assert not has_strong_rtl("library")
    assert bidi_controls_in("a‮b‮c") == ("U+202E",)
    assert bidi_controls_in("plain") == ()
