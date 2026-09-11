"""Static validation of a case directory, before a run spends a request.

The loader is already strict, but it only speaks when ``gauntlet run`` starts,
and the UNSCOREABLE refusal only speaks after the target has answered. A team
whose first suite is adversarial-only learns in CI, having paid for the
requests, that nothing it wrote could have failed on silence.

``gauntlet lint DIR`` moves both checks to the editor. It reuses the loader, so
a schema, enum, duplicate-id, threshold, or ``.yml`` problem produces the same
located error ``run`` produces, and it adds the analysis ``run`` can only do
afterwards: whether any loaded suite could fail a target that says nothing.
Linting contacts no target, needs no model and no network, and gives the same
answer every time, so it can run on every commit at no cost.

Two things it refuses to do. It never rewrites a file: a linter that fixes
suites is a linter that can quietly change what a gate measures. And it never
reports a scoreability verdict it could not reach: when a case file failed to
load, the suite it would have contributed is unknown, so the analysis is
reported as not run rather than as a clean result over whatever happened to
parse.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from gauntlet.cases import (
    CaseFileError,
    Suite,
    load_suite_text,
)
from gauntlet.gates import scores_capability

ERROR = "error"
WARNING = "warning"

_SEVERITY_ORDER = {ERROR: 0, WARNING: 1}

REMEDIES = (
    "Add a false_positive or golden suite, a grounding case with "
    "expect_grounded: true, or a refusal case of kind: crisis."
)


@dataclass(frozen=True)
class Finding:
    """One problem, with the file it is in and a code a script can match on."""

    severity: str
    code: str
    source: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "severity": self.severity,
            "code": self.code,
            "source": self.source,
            "message": self.message,
        }

    @property
    def sort_key(self) -> tuple[int, str, str, str]:
        return (_SEVERITY_ORDER[self.severity], self.source, self.code, self.message)


@dataclass(frozen=True)
class LintReport:
    """What linting one directory found, and what it was able to look at."""

    directory: str
    findings: tuple[Finding, ...]
    suites_loaded: int
    cases_loaded: int
    scoreability_analysed: bool

    @property
    def errors(self) -> tuple[Finding, ...]:
        return tuple(item for item in self.findings if item.severity == ERROR)

    @property
    def warnings(self) -> tuple[Finding, ...]:
        return tuple(item for item in self.findings if item.severity == WARNING)

    @property
    def ok(self) -> bool:
        """Whether these suites are fit to run. Warnings do not decide this."""
        return not self.errors

    def to_dict(self) -> dict[str, object]:
        return {
            "directory": self.directory,
            "suites_loaded": self.suites_loaded,
            "cases_loaded": self.cases_loaded,
            "scoreability_analysed": self.scoreability_analysed,
            "errors": len(self.errors),
            "warnings": len(self.warnings),
            "findings": [item.to_dict() for item in self.findings],
        }


def _error(code: str, source: str, message: str) -> Finding:
    return Finding(severity=ERROR, code=code, source=source, message=message)


def _warning(code: str, source: str, message: str) -> Finding:
    return Finding(severity=WARNING, code=code, source=source, message=message)


def _extension_findings(directory: Path) -> list[Finding]:
    """The two directory-level refusals, worded exactly as the loader words them.

    A ``.yml`` file is not a file to skip: skipping it drops every case the
    operator wrote in it, under a verdict that does not say so.
    """
    misnamed = sorted(path.name for path in directory.glob("*.yml"))
    if not misnamed:
        return []
    return [
        _error(
            "misnamed_extension",
            str(directory),
            f"case files must end in '.yaml', but found {misnamed}. "
            f"Rename them rather than have their cases silently not run.",
        )
    ]


def _load_findings(paths: Iterable[Path]) -> tuple[list[Suite], list[Finding]]:
    suites: list[Suite] = []
    findings: list[Finding] = []
    for path in paths:
        try:
            suites.append(load_suite_text(path.read_text(encoding="utf-8"), str(path)))
        except CaseFileError as exc:
            findings.append(_error("schema", str(path), str(exc)))
    return suites, findings


def _duplicate_gate_findings(suites: Iterable[Suite]) -> list[Finding]:
    seen: dict[str, str] = {}
    findings: list[Finding] = []
    for suite in suites:
        if suite.gate in seen:
            findings.append(
                _error(
                    "duplicate_gate",
                    suite.source,
                    f"gate {suite.gate!r} already provided by {seen[suite.gate]}",
                )
            )
            continue
        seen[suite.gate] = suite.source
    return findings


def _judge_calibration_findings(suites: Iterable[Suite]) -> list[Finding]:
    """A judge suite whose calibration set is not on disk withholds every verdict.

    This is statically knowable and always fatal to the run's verdict, which is
    what makes it a lint error rather than something to discover after the
    model has been called.
    """
    findings: list[Finding] = []
    for suite in suites:
        path = suite.calibration_path()
        if path is None or path.is_file():
            continue
        findings.append(
            _error(
                "judge_calibration_missing",
                suite.source,
                f"suite {suite.name!r} names calibration set {str(path)!r}, "
                f"which is not a file. An uncalibrated judge's verdicts do not count, "
                f"so the run would report no verdict at all.",
            )
        )
    return findings


def _language_findings(suites: Iterable[Suite]) -> list[Finding]:
    """Every language a suite declares is a peer of every other. One scored is half a gate.

    The peer rule CONTRIBUTING.md states for English and Spanish is checked here
    against whatever the suite declares, so a suite covering ``[en, es, ar]``
    is held to three-way peering and a suite that declares nothing is held to
    the same two languages it always was.

    An excepted language is neither an error nor part of the balance
    comparison, but it is still reported: an exception that produces no output
    is a check that has been turned off invisibly, and the point of requiring a
    reason was that somebody reads it.
    """
    findings: list[Finding] = []
    for suite in suites:
        excepted = suite.excepted_languages()
        for exception in sorted(suite.coverage_exceptions, key=lambda item: item.language):
            findings.append(
                _warning(
                    "language_excepted",
                    suite.source,
                    f"suite {suite.name!r} declares {exception.language!r} and covers "
                    f"none of it, by recorded exception: {exception.reason}",
                )
            )
        counts = {
            language: sum(1 for case in suite.cases if case.language == language)
            for language in suite.languages
            if language not in excepted
        }
        absent = sorted(language for language, count in counts.items() if count == 0)
        if absent:
            present = sorted(set(counts) - set(absent))
            findings.append(
                _error(
                    "missing_language",
                    suite.source,
                    f"suite {suite.name!r} has no {', '.join(absent)} cases. "
                    f"A suite's declared languages are peers, so this gate would score "
                    f"only {', '.join(present) or 'nothing'}.",
                )
            )
            continue
        if len(set(counts.values())) > 1:
            spread = ", ".join(f"{count} {language}" for language, count in sorted(counts.items()))
            findings.append(
                _warning(
                    "language_imbalance",
                    suite.source,
                    f"suite {suite.name!r} has {spread}. Peers are added and changed "
                    f"together, so a gate that measures more in one language reports a "
                    f"pass rate weighted toward it.",
                )
            )
    return findings


def _duplicate_prompt_findings(suites: Iterable[Suite]) -> list[Finding]:
    """The same prompt asked twice under one gate is one observation counted twice.

    Deliberately scoped to a single suite. Issue #49 asked for duplicates
    "across suites", and the built-in suites are the counter-example: the same
    corpus question appears in `grounding`, `golden`, and `false_positive` on
    purpose, because each gate asks something different of the same answer. A
    warning there would fire six times on this project's own cases, and the
    same issue requires those suites to lint clean. Within one suite there is
    no such reading: one gate, one prompt, two case ids is duplication.

    A multi-turn case is one observation of its whole conversation, so it is
    keyed by every turn. Two escalations that share a benign opener are two
    observations; two with the same turns are one.
    """
    findings: list[Finding] = []
    for suite in suites:
        where: dict[tuple[str, ...], list[str]] = {}
        for case in suite.cases:
            key = tuple(turn.prompt for turn in case.turns) if case.turns else (case.prompt,)
            where.setdefault(key, []).append(case.id)
        findings.extend(
            _warning("duplicate_prompt", suite.source, _duplicate_message(suite.name, key, ids))
            for key, ids in sorted(where.items())
            if len(ids) > 1
        )
    return findings


def _duplicate_message(suite: str, key: tuple[str, ...], ids: list[str]) -> str:
    held = (
        f"holds the same {len(key)}-turn conversation" if len(key) > 1 else "asks the same prompt"
    )
    return (
        f"suite {suite!r} {held} in cases {', '.join(sorted(ids))}. One observation "
        f"counted twice does not measure twice as much."
    )


def _scoreability_findings(suites: list[Suite]) -> list[Finding]:
    """Whether anything here could fail a target that says nothing."""
    if any(scores_capability(suite) for suite in suites):
        return []
    gates = ", ".join(sorted({suite.gate for suite in suites}))
    return [
        _error(
            "unscoreable",
            ", ".join(sorted(suite.source for suite in suites)),
            f"no loaded suite scores whether the target can answer at all "
            f"(loaded gates: {gates}). A run over these suites would be UNSCOREABLE "
            f"as soon as the target returned anything unreadable, because a pass rate "
            f"from absence-phrased checks alone is satisfied by silence. {REMEDIES}",
        )
    ]


def lint_directory(directory: Path) -> LintReport:
    """Check one case directory without contacting anything."""
    if not directory.is_dir():
        return LintReport(
            directory=str(directory),
            findings=(_error("directory_missing", str(directory), "case directory not found"),),
            suites_loaded=0,
            cases_loaded=0,
            scoreability_analysed=False,
        )
    findings = _extension_findings(directory)
    paths = sorted(directory.glob("*.yaml"))
    if not paths:
        findings.append(_error("no_case_files", str(directory), "no *.yaml case files"))
        return _report(directory, findings, [], scoreability_analysed=False)
    suites, load_findings = _load_findings(paths)
    findings += load_findings
    findings += _duplicate_gate_findings(suites)
    findings += _judge_calibration_findings(suites)
    findings += _language_findings(suites)
    findings += _duplicate_prompt_findings(suites)
    # Scoreability is a statement about the whole directory. A file that did not
    # load might have been the golden suite that makes the rest scoreable, so
    # the analysis is withheld rather than run over what happened to parse.
    analysed = not load_findings
    if analysed:
        findings += _scoreability_findings(suites)
    return _report(directory, findings, suites, scoreability_analysed=analysed)


def _report(
    directory: Path,
    findings: list[Finding],
    suites: list[Suite],
    *,
    scoreability_analysed: bool,
) -> LintReport:
    return LintReport(
        directory=str(directory),
        findings=tuple(sorted(findings, key=lambda item: item.sort_key)),
        suites_loaded=len(suites),
        cases_loaded=sum(len(suite.cases) for suite in suites),
        scoreability_analysed=scoreability_analysed,
    )


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def render_lint_text(report: LintReport) -> str:
    """One line per finding, then what was looked at and what was found."""
    lines = [f"{item.severity}: {item.source}: {item.message}" for item in report.findings]
    lines.append(
        f"{_plural(report.suites_loaded, 'suite')}, "
        f"{_plural(report.cases_loaded, 'case')}. "
        f"{_plural(len(report.errors), 'error')}, "
        f"{_plural(len(report.warnings), 'warning')}."
    )
    if not report.scoreability_analysed:
        lines.append(
            "scoreability was not analysed, because not every case file loaded. "
            "Fix the errors above and lint again."
        )
    return "\n".join(lines) + "\n"
