"""Command-line interface: ``gauntlet run`` and ``gauntlet report``.

``run`` evaluates a target against a directory of case files (or the
built-in bilingual suites) and writes a results JSON, exiting non-zero if
any gate fails. ``report`` turns a results JSON into a Markdown summary.

The default target is the in-repo toy, so the CLI is demonstrable with no
network and no configuration. Real targets are selected with ``--http-url``
or, for a Python callable, ``--callable path.to:factory``.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from gauntlet.cases import Suite, builtin_suites, load_suites
from gauntlet.gates import run_suite
from gauntlet.report import render_markdown
from gauntlet.results import ResultsFileError, RunResult, load_run_dict, now_iso
from gauntlet.targets import CallableTarget, HttpTarget, Target
from gauntlet.toy import ToyRag


def _load_callable_target(spec: str) -> Target:
    if ":" not in spec:
        raise ValueError(f"--callable must be 'module.path:factory', got {spec!r}")
    module_name, _, attr = spec.partition(":")
    module = importlib.import_module(module_name)
    factory = getattr(module, attr)
    produced = factory()
    if not (hasattr(produced, "ask") and hasattr(produced, "name")):
        raise ValueError(f"{spec} did not produce a target with .ask and .name")
    return CallableTarget(fn=produced.ask, name=produced.name)


def _select_target(args: argparse.Namespace) -> Target:
    chosen = [bool(args.http_url), bool(args.callable)]
    if sum(chosen) > 1:
        raise ValueError("choose at most one of --http-url or --callable")
    if args.http_url:
        return HttpTarget(url=args.http_url)
    if args.callable:
        return _load_callable_target(args.callable)
    return ToyRag()


def _select_suites(args: argparse.Namespace) -> tuple[Suite, ...]:
    if args.cases:
        return load_suites(Path(args.cases))
    return builtin_suites()


def _cmd_run(args: argparse.Namespace) -> int:
    target = _select_target(args)
    suites = _select_suites(args)
    gates = tuple(run_suite(suite, target) for suite in suites)
    run = RunResult(target=target.name, gates=gates, started_at=now_iso())
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        run.write_json(out_path)
    _print_run_summary(run)
    return 0 if run.passed else 1


def _print_run_summary(run: RunResult) -> None:
    print(f"target: {run.target}")
    for gate in run.gates:
        status = "PASS" if gate.passed else "FAIL"
        by_lang = gate.counts_by_language()
        lang_str = ", ".join(
            f"{lang} {bucket['passed']}/{bucket['total']}" for lang, bucket in by_lang.items()
        )
        print(
            f"  [{status}] {gate.gate}: {gate.passed_count}/{gate.total} "
            f"(threshold {gate.threshold:g}; {lang_str})"
        )
    print("overall:", "PASS" if run.passed else "FAIL")


def _cmd_report(args: argparse.Namespace) -> int:
    run = load_run_dict(Path(args.results))
    if args.format == "json":
        rendered = json.dumps(run, indent=2, sort_keys=False) + "\n"
    else:
        rendered = render_markdown(run)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered, encoding="utf-8")
        print(f"wrote {args.format} report to {out_path}")
    else:
        print(rendered)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gauntlet", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="evaluate a target against the gates")
    run_parser.add_argument("--cases", help="directory of *.yaml case files (default: built-ins)")
    run_parser.add_argument("--http-url", help="evaluate an HTTP endpoint target")
    run_parser.add_argument("--callable", help="evaluate a Python target 'module:factory'")
    run_parser.add_argument("--out", help="write the results JSON to this path")
    run_parser.set_defaults(func=_cmd_run)

    report_parser = sub.add_parser("report", help="render a results JSON as a report")
    report_parser.add_argument("results", help="path to a results JSON from 'gauntlet run'")
    report_parser.add_argument(
        "--format", choices=("md", "json"), default="md", help="output format (default: md)"
    )
    report_parser.add_argument("--out", help="write the report to this path")
    report_parser.set_defaults(func=_cmd_report)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = args.func(args)
    except (ValueError, ResultsFileError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return int(result)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
