"""Command-line interface: ``gauntlet run``, ``report``, and ``inventory``.

``run`` evaluates a target against a directory of case files (or the
built-in bilingual suites) and writes a results JSON, exiting non-zero if
any gate fails. ``report`` turns one results JSON (optionally with a
baseline results JSON for whole-run drift) into the evidence pack, in
machine-readable JSON or as a human-readable document. ``inventory`` prints
the gate inventory with counts taken from the loaded suites.

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
from gauntlet.evidence import build_evidence_pack, github_output_lines
from gauntlet.gates import run_suite
from gauntlet.inventory import (
    BEGIN_MARKER,
    build_inventory,
    render_inventory_markdown,
    update_marked_block,
)
from gauntlet.report import render_markdown
from gauntlet.results import RunResult, load_run_dict, now_iso
from gauntlet.targets import CallableTarget, HttpTarget, Target
from gauntlet.toy import ToyRag


def _load_callable_target(spec: str) -> Target:
    if ":" not in spec:
        raise ValueError(f"--callable must be 'module.path:factory', got {spec!r}")
    module_name, _, attr = spec.partition(":")
    # --callable is arbitrary code execution by design: the operator names a
    # module and Gauntlet imports it. Putting the working directory on the
    # import path is what makes that usable from a consumer's own repository,
    # where the target module is not installed. SECURITY.md says so plainly.
    cwd = str(Path.cwd())
    if cwd not in sys.path:
        sys.path.insert(0, cwd)
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


def _select_suites(cases: str | None) -> tuple[Suite, ...]:
    if cases:
        return load_suites(Path(cases))
    return builtin_suites()


def _write(path: str, text: str) -> Path:
    out_path = Path(path)
    if out_path.parent != Path():
        out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    return out_path


def _cmd_run(args: argparse.Namespace) -> int:
    target = _select_target(args)
    suites = _select_suites(args.cases)
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
    baseline = load_run_dict(Path(args.baseline)) if args.baseline else None
    pack = build_evidence_pack(run, baseline)
    if args.format == "json":
        rendered = json.dumps(pack, indent=2, sort_keys=False, ensure_ascii=False) + "\n"
    else:
        rendered = render_markdown(pack)
    if args.out:
        out_path = _write(args.out, rendered)
        print(f"wrote {args.format} evidence pack to {out_path}")
    else:
        print(rendered)
    if args.github_output:
        _append_github_output(Path(args.github_output), pack)
    return 0


def _append_github_output(path: Path, pack: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for line in github_output_lines(pack):
            handle.write(line + "\n")


def _cmd_inventory(args: argparse.Namespace) -> int:
    inventory = build_inventory(_select_suites(args.cases))
    if args.format == "json":
        rendered = json.dumps(inventory.to_dict(), indent=2, sort_keys=False) + "\n"
    else:
        rendered = render_inventory_markdown(inventory) + "\n"
    if args.update:
        target = Path(args.update)
        document = target.read_text(encoding="utf-8")
        block = render_inventory_markdown(inventory)
        target.write_text(update_marked_block(document, block), encoding="utf-8")
        print(f"updated the {BEGIN_MARKER} block in {target}")
        return 0
    print(rendered, end="")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gauntlet", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    _add_run_parser(sub)
    _add_report_parser(sub)
    _add_inventory_parser(sub)
    return parser


def _add_run_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    run_parser = sub.add_parser("run", help="evaluate a target against the gates")
    run_parser.add_argument("--cases", help="directory of *.yaml case files (default: built-ins)")
    run_parser.add_argument("--http-url", help="evaluate an HTTP endpoint target")
    run_parser.add_argument("--callable", help="evaluate a Python target 'module:factory'")
    run_parser.add_argument("--out", help="write the results JSON to this path")
    run_parser.set_defaults(func=_cmd_run)


def _add_report_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    report_parser = sub.add_parser("report", help="build the evidence pack from a results JSON")
    report_parser.add_argument("results", help="path to a results JSON from 'gauntlet run'")
    report_parser.add_argument(
        "--baseline",
        help="path to an earlier results JSON, to report whole-run drift against it",
    )
    report_parser.add_argument(
        "--format",
        choices=("md", "json"),
        default="md",
        help="md for the human-readable document, json for the machine-readable pack",
    )
    report_parser.add_argument("--out", help="write the evidence pack to this path")
    report_parser.add_argument(
        "--github-output",
        help="append GitHub Actions 'name=value' output lines to this file",
    )
    report_parser.set_defaults(func=_cmd_report)


def _add_inventory_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    inventory_parser = sub.add_parser(
        "inventory", help="print the gate inventory with counts taken from the suites"
    )
    inventory_parser.add_argument(
        "--cases", help="directory of *.yaml case files (default: built-ins)"
    )
    inventory_parser.add_argument("--format", choices=("md", "json"), default="md")
    inventory_parser.add_argument(
        "--update", help="rewrite the generated inventory block in this Markdown file"
    )
    inventory_parser.set_defaults(func=_cmd_inventory)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = args.func(args)
    # ValueError covers CaseFileError, ResultsFileError, and InventoryError;
    # OSError covers an unreadable or unwritable path.
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return int(result)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
