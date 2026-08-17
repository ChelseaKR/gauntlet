"""Case-file schema validation, including its rejections."""

from __future__ import annotations

from pathlib import Path

import pytest

from gauntlet.cases import (
    CaseFileError,
    Suite,
    builtin_suites,
    iter_case_counts,
    load_suite_text,
    load_suites,
)

_VALID_GROUNDING = """
suite: t
gate: grounding
version: 1
cases:
  - id: a
    language: en
    prompt: hello
    expect_grounded: false
    must_contain: []
"""


def test_valid_suite_loads() -> None:
    suite = load_suite_text(_VALID_GROUNDING, "t")
    assert suite.gate == "grounding"
    assert suite.threshold == 1.0
    assert len(suite.cases) == 1


def test_builtin_suites_load_and_cover_all_gates() -> None:
    suites = builtin_suites()
    gates = {s.gate for s in suites}
    assert gates == {"grounding", "adversarial", "refusal", "false_positive", "golden"}


def test_iter_case_counts_are_counted() -> None:
    counts = list(iter_case_counts(builtin_suites()))
    adversarial = {(lang, n) for gate, lang, n in counts if gate == "adversarial"}
    assert ("en", 12) in adversarial
    assert ("es", 12) in adversarial


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("gate: grounding\n", "top level must be a mapping"),
    ],
)
def test_non_mapping_top_level(mutation: str, message: str) -> None:
    with pytest.raises(CaseFileError, match=message):
        load_suite_text("- 1\n- 2\n", "bad")


def test_unknown_suite_key_rejected() -> None:
    text = _VALID_GROUNDING + "extra: nope\n"
    with pytest.raises(CaseFileError, match="unknown suite keys"):
        load_suite_text(text, "bad")


def test_unknown_gate_rejected() -> None:
    text = _VALID_GROUNDING.replace("gate: grounding", "gate: telepathy")
    with pytest.raises(CaseFileError, match="'gate' must be one of"):
        load_suite_text(text, "bad")


def test_bad_version_rejected() -> None:
    text = _VALID_GROUNDING.replace("version: 1", "version: 0")
    with pytest.raises(CaseFileError, match="'version' must be a positive integer"):
        load_suite_text(text, "bad")


def test_bool_version_rejected() -> None:
    text = _VALID_GROUNDING.replace("version: 1", "version: true")
    with pytest.raises(CaseFileError, match="'version' must be a positive integer"):
        load_suite_text(text, "bad")


def test_bad_threshold_rejected() -> None:
    text = _VALID_GROUNDING.replace("version: 1", "version: 1\nthreshold: 2.0")
    with pytest.raises(CaseFileError, match="'threshold' must be a number"):
        load_suite_text(text, "bad")


def test_bool_threshold_rejected() -> None:
    text = _VALID_GROUNDING.replace("version: 1", "version: 1\nthreshold: true")
    with pytest.raises(CaseFileError, match="'threshold' must be a number"):
        load_suite_text(text, "bad")


def test_zero_threshold_rejected() -> None:
    # A gate that passes at nothing passed cannot fail, and the run summary
    # would print "[PASS] refusal: 0/2". A gate that cannot fail is not a gate.
    text = _VALID_GROUNDING.replace("version: 1", "version: 1\nthreshold: 0")
    with pytest.raises(CaseFileError, match="unable to fail"):
        load_suite_text(text, "bad")


def test_the_smallest_usable_threshold_is_still_accepted() -> None:
    # Rejecting 0 must not reject a genuinely lenient threshold.
    text = _VALID_GROUNDING.replace("version: 1", "version: 1\nthreshold: 0.01")
    assert load_suite_text(text, "ok").threshold == 0.01


def test_empty_cases_rejected() -> None:
    text = "suite: t\ngate: grounding\nversion: 1\ncases: []\n"
    with pytest.raises(CaseFileError, match="'cases' must be a non-empty list"):
        load_suite_text(text, "bad")


def test_unknown_case_key_rejected() -> None:
    text = _VALID_GROUNDING.replace("must_contain: []", "must_contain: []\n    bogus: 1")
    with pytest.raises(CaseFileError, match="unknown keys for gate"):
        load_suite_text(text, "bad")


def test_missing_required_case_key_rejected() -> None:
    text = """
suite: t
gate: grounding
version: 1
cases:
  - id: a
    prompt: hello
    expect_grounded: false
"""
    with pytest.raises(CaseFileError, match="missing required keys"):
        load_suite_text(text, "bad")


def test_bad_language_rejected() -> None:
    text = _VALID_GROUNDING.replace("language: en", "language: fr")
    with pytest.raises(CaseFileError, match="'language' must be one of"):
        load_suite_text(text, "bad")


def test_duplicate_case_id_rejected() -> None:
    text = """
suite: t
gate: grounding
version: 1
cases:
  - id: dup
    language: en
    prompt: hello
    expect_grounded: false
    must_contain: []
  - id: dup
    language: es
    prompt: hola
    expect_grounded: false
    must_contain: []
"""
    with pytest.raises(CaseFileError, match="duplicate case id"):
        load_suite_text(text, "bad")


def test_grounding_requires_bool_expect_grounded() -> None:
    text = _VALID_GROUNDING.replace("expect_grounded: false", "expect_grounded: maybe")
    with pytest.raises(CaseFileError, match="'expect_grounded' must be a boolean"):
        load_suite_text(text, "bad")


def test_adversarial_requires_known_attack_type() -> None:
    text = """
suite: t
gate: adversarial
version: 1
cases:
  - id: a
    language: en
    prompt: hi
    attack_type: telepathy
    must_not_contain: ["x"]
"""
    with pytest.raises(CaseFileError, match="'attack_type' must be one of"):
        load_suite_text(text, "bad")


def test_adversarial_requires_markers() -> None:
    text = """
suite: t
gate: adversarial
version: 1
cases:
  - id: a
    language: en
    prompt: hi
    attack_type: jailbreak
    must_not_contain: []
"""
    with pytest.raises(CaseFileError, match="at least one marker"):
        load_suite_text(text, "bad")


def test_refusal_requires_known_kind() -> None:
    text = """
suite: t
gate: refusal
version: 1
cases:
  - id: a
    language: en
    prompt: hi
    kind: ignore
    must_contain: []
"""
    with pytest.raises(CaseFileError, match="'kind' must be one of"):
        load_suite_text(text, "bad")


def test_golden_requires_key_version() -> None:
    text = """
suite: t
gate: golden
version: 1
cases:
  - id: a
    language: en
    prompt: hi
    expected: hello
"""
    with pytest.raises(CaseFileError, match="golden suites require"):
        load_suite_text(text, "bad")


def test_key_version_rejected_on_non_golden() -> None:
    text = _VALID_GROUNDING.replace("version: 1", "version: 1\nkey_version: 1")
    with pytest.raises(CaseFileError, match="only valid for golden"):
        load_suite_text(text, "bad")


def test_invalid_yaml_rejected() -> None:
    with pytest.raises(CaseFileError, match="invalid YAML"):
        load_suite_text("key: : :\n  - [", "bad")


def test_case_must_be_mapping() -> None:
    text = "suite: t\ngate: grounding\nversion: 1\ncases:\n  - just a string\n"
    with pytest.raises(CaseFileError, match="each case must be a mapping"):
        load_suite_text(text, "bad")


def test_load_suites_from_directory(tmp_path: Path) -> None:
    (tmp_path / "g.yaml").write_text(_VALID_GROUNDING, encoding="utf-8")
    suites = load_suites(tmp_path)
    assert len(suites) == 1


def test_load_suites_missing_dir(tmp_path: Path) -> None:
    with pytest.raises(CaseFileError, match="case directory not found"):
        load_suites(tmp_path / "nope")


def test_load_suites_empty_dir(tmp_path: Path) -> None:
    with pytest.raises(CaseFileError, match=r"no \*.yaml case files"):
        load_suites(tmp_path)


def test_duplicate_gate_across_files_rejected(tmp_path: Path) -> None:
    (tmp_path / "a.yaml").write_text(_VALID_GROUNDING, encoding="utf-8")
    (tmp_path / "b.yaml").write_text(_VALID_GROUNDING, encoding="utf-8")
    with pytest.raises(CaseFileError, match="already provided by"):
        load_suites(tmp_path)


def test_string_field_must_be_non_empty() -> None:
    text = _VALID_GROUNDING.replace("prompt: hello", "prompt: ''")
    with pytest.raises(CaseFileError, match="'prompt' must be a non-empty string"):
        load_suite_text(text, "bad")


def test_str_list_must_be_strings() -> None:
    text = """
suite: t
gate: refusal
version: 1
cases:
  - id: a
    language: en
    prompt: hi
    kind: crisis
    must_contain: [1, 2]
"""
    with pytest.raises(CaseFileError, match="must be a list of non-empty strings"):
        load_suite_text(text, "bad")


def test_suite_source_recorded() -> None:
    suite: Suite = load_suite_text(_VALID_GROUNDING, "source-name")
    assert suite.source == "source-name"


_VALID_FALSE_POSITIVE = """
suite: fp
gate: false_positive
version: 1
cases:
  - id: fp-a
    language: en
    prompt: What are the library hours?
    must_contain: ["9:00"]
"""


def test_a_yml_case_file_is_an_error_not_a_silent_skip(tmp_path: Path) -> None:
    """The extension mismatch that drops a whole suite without saying so.

    A directory holding `grounding.yaml` and `false_positive.yml` used to load
    only the first, and the run reported PASS over the half that loaded. The
    dropped half was the suite that scores whether the target can answer.
    """
    (tmp_path / "grounding.yaml").write_text(_VALID_GROUNDING, encoding="utf-8")
    (tmp_path / "false_positive.yml").write_text(_VALID_FALSE_POSITIVE, encoding="utf-8")
    with pytest.raises(CaseFileError, match=r"must end in '\.yaml'"):
        load_suites(tmp_path)


def test_a_directory_of_yaml_files_still_loads(tmp_path: Path) -> None:
    (tmp_path / "grounding.yaml").write_text(_VALID_GROUNDING, encoding="utf-8")
    (tmp_path / "false_positive.yaml").write_text(_VALID_FALSE_POSITIVE, encoding="utf-8")
    assert {suite.gate for suite in load_suites(tmp_path)} == {"grounding", "false_positive"}
