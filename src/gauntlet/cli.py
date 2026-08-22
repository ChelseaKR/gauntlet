"""Command-line interface: ``gauntlet run``, ``report``, ``inventory``, ``site``.

``run`` evaluates a target against a directory of case files (or the
built-in bilingual suites) and writes a results JSON, exiting non-zero if
any gate fails. ``report`` turns one results JSON (optionally with a
baseline results JSON for whole-run drift) into the evidence pack, in
machine-readable JSON or as a human-readable document. ``inventory`` prints
the gate inventory with counts taken from the loaded suites. ``site``
renders the documentation site from the harness: the counts are the
inventory's, and the evidence excerpts are runs made while it builds.

The default target is the in-repo toy, so the CLI is demonstrable with no
network and no configuration. Real targets are selected with ``--http-url``
or, for a Python callable, ``--callable path.to:factory``. The default only
applies to the zero-configuration demo: supplying ``--cases`` without a target
is a misconfiguration, not a request to evaluate a fictional city's toy
assistant, and it is refused rather than answered with a green verdict.

Exit codes: 0 a clean run, 1 a gate below its threshold, 2 the harness itself
could not run, 4 the run could not be scored.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from gauntlet.cases import Suite, builtin_suites, load_suites
from gauntlet.evidence import build_evidence_pack, github_output_lines
from gauntlet.gates import judge_withheld_reason, run_suite, unscoreable_reason
from gauntlet.inventory import (
    BEGIN_MARKER,
    build_inventory,
    render_inventory_markdown,
    update_marked_block,
)
from gauntlet.judge import DEFAULT_JUDGE_REGION, BedrockJudge, Judge, JudgeError, RecordingJudge
from gauntlet.report import render_markdown
from gauntlet.results import RunResult, load_run_dict, now_iso, run_summary_lines
from gauntlet.site import build_site
from gauntlet.targets import (
    CallableTarget,
    HttpTarget,
    Target,
    TargetError,
    target_provenance,
)
from gauntlet.toy import ToyRag

# A run the harness refuses to score. Distinct from 1 (a gate failed, which is
# the gates working) and from 2 (the harness could not run at all).
EXIT_UNSCOREABLE = 4
UNSCOREABLE_VERDICT = "UNSCOREABLE"


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
    return CallableTarget(
        fn=produced.ask,
        name=produced.name,
        provenance_fn=lambda: target_provenance(produced),
    )


def _parse_provenance(pairs: Sequence[str] | None) -> dict[str, str]:
    """``--provenance key=value`` flags, each a non-empty key and value."""
    parsed: dict[str, str] = {}
    for pair in pairs or ():
        key, separator, value = pair.partition("=")
        if not separator or not key.strip() or not value.strip():
            raise ValueError(
                f"--provenance expects KEY=VALUE with both sides non-empty, got {pair!r}"
            )
        parsed[key.strip()] = value.strip()
    return parsed


def _assemble_provenance(
    target: Target, started_at: str, flags: Sequence[str] | None, judge: Judge | None = None
) -> dict[str, str]:
    """Target-reported provenance, then the operator's flags on top.

    The operator's flags win because the operator is the one committing the
    pack and answering for it. The date defaults to the run's own UTC date;
    nothing else is defaulted, because a defaulted model or commit would be a
    value the harness invented.
    """
    provenance = target_provenance(target)
    provenance.setdefault("target", target.name)
    provenance.setdefault("date", started_at[:10])
    if judge is not None:
        provenance.setdefault("judge_model", judge.model)
    provenance.update(_parse_provenance(flags))
    return provenance


def _select_target(args: argparse.Namespace) -> Target:
    chosen = [bool(args.http_url), bool(args.callable)]
    if sum(chosen) > 1:
        raise ValueError("choose at most one of --http-url or --callable")
    if args.http_url:
        return HttpTarget(url=args.http_url)
    if args.callable:
        return _load_callable_target(args.callable)
    if getattr(args, "cases", None):
        # Falling back to the toy here would evaluate a fictional city's demo
        # assistant against the operator's own cases and report the verdict as
        # theirs. In CI that is a green check on a feature nothing contacted.
        raise ValueError(
            "--cases was given without a target, and the built-in toy is not your system. "
            "Pass --http-url or --callable. To evaluate the toy on purpose, name it: "
            "--callable gauntlet.toy:ToyRag"
        )
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


def _claim_out_path(path_str: str) -> Path:
    """Take ownership of the results path before the run starts.

    After ``gauntlet run --out X``, X holds this run's results or does not
    exist. It is never left holding an earlier run's. A run that aborts partway
    writes nothing, and if a previous file were left in place, the next command
    in the pipeline would build an evidence pack out of it and present a stale
    verdict as this run's: the shape a reviewer cannot see from the pack, since
    a stale pack looks exactly like a fresh one.
    """
    out_path = Path(path_str)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.unlink(missing_ok=True)
    return out_path


def _select_judge(args: argparse.Namespace) -> Judge | None:
    """The judge a judge suite will use, or None when none was configured.

    A replay recording makes a judge on its own: the verdicts it holds are the
    recorded model's. Otherwise a model name, from the flag or the environment,
    names a Bedrock judge, optionally wrapped to record its verdicts. Nothing
    here contacts the model; the first grade does.
    """
    replay = getattr(args, "judge_replay", None)
    record = getattr(args, "judge_record", None)
    model = getattr(args, "judge_model", None) or os.environ.get("GAUNTLET_JUDGE_MODEL", "")
    if replay:
        return RecordingJudge(replay_path=Path(replay))
    if not model:
        return None
    region = getattr(args, "judge_region", None) or os.environ.get(
        "AWS_REGION", DEFAULT_JUDGE_REGION
    )
    inner = BedrockJudge(model=model, region=region)
    if record:
        return RecordingJudge(inner=inner, write_path=Path(record))
    return inner


def _cmd_run(args: argparse.Namespace) -> int:
    target = _select_target(args)
    suites = _select_suites(args.cases)
    judge = _select_judge(args)
    out_path = _claim_out_path(args.out) if args.out else None
    gates = tuple(run_suite(suite, target, judge) for suite in suites)
    scored = RunResult(target=target.name, gates=gates, started_at=now_iso())
    withheld = unscoreable_reason(scored, suites) or judge_withheld_reason(scored)
    # The reason travels with the results file, so a pack rendered from it
    # later cannot report a verdict this run declined to reach. Provenance is
    # read after the run so the target's counters are final.
    run = RunResult(
        target=scored.target,
        gates=scored.gates,
        started_at=scored.started_at,
        verdict_withheld=withheld,
        provenance=_assemble_provenance(target, scored.started_at, args.provenance, judge),
    )
    if out_path is not None:
        run.write_json(out_path)
    _print_run_summary(run, verdict=UNSCOREABLE_VERDICT if withheld else None)
    if withheld:
        print(f"error: {withheld}", file=sys.stderr)
        return EXIT_UNSCOREABLE
    return 0 if run.passed else 1


def _print_run_summary(run: RunResult, verdict: str | None = None) -> None:
    for line in run_summary_lines(run, verdict=verdict):
        print(line)


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


def _cmd_site(args: argparse.Namespace) -> int:
    written = build_site(
        Path(args.out),
        action_file=Path(args.action_file),
        generated=args.generated or "",
    )
    for path in written:
        print(f"wrote {path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gauntlet", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    _add_run_parser(sub)
    _add_report_parser(sub)
    _add_inventory_parser(sub)
    _add_site_parser(sub)
    return parser


def _add_run_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    run_parser = sub.add_parser("run", help="evaluate a target against the gates")
    run_parser.add_argument("--cases", help="directory of *.yaml case files (default: built-ins)")
    run_parser.add_argument("--http-url", help="evaluate an HTTP endpoint target")
    run_parser.add_argument("--callable", help="evaluate a Python target 'module:factory'")
    run_parser.add_argument("--out", help="write the results JSON to this path")
    run_parser.add_argument(
        "--judge-model",
        help="Bedrock model id for judge suites (default: GAUNTLET_JUDGE_MODEL from the "
        "environment); without one, a judge suite's verdicts do not count and the run has "
        "no verdict",
    )
    run_parser.add_argument(
        "--judge-region", help="AWS region for the judge (default: AWS_REGION or us-west-2)"
    )
    run_parser.add_argument(
        "--judge-record", help="write every judge verdict to this JSON Lines file"
    )
    run_parser.add_argument(
        "--judge-replay",
        help="take judge verdicts from this recording instead of a model; no model is called",
    )
    run_parser.add_argument(
        "--provenance",
        action="append",
        metavar="KEY=VALUE",
        help="record where this run came from (target_version, model, prompt_version, "
        "commit, ...); repeatable, and the operator's values override the target's",
    )
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


def _add_site_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    site_parser = sub.add_parser("site", help="render the documentation site from the harness")
    site_parser.add_argument("--out", default="site", help="directory to write the pages to")
    site_parser.add_argument(
        "--action-file",
        default="action.yml",
        help="action definition the inputs and outputs tables are read from",
    )
    site_parser.add_argument(
        "--generated",
        default="",
        help="date to print in the footer; omit to keep the build free of a clock",
    )
    site_parser.set_defaults(func=_cmd_site)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = args.func(args)
    # ValueError covers CaseFileError, ResultsFileError, and InventoryError;
    # OSError covers an unreadable or unwritable path; TargetError covers the
    # target being unreachable, breaking its contract, or raising on its own.
    # All three are the harness not completing a run, which is exit 2. None of
    # them is a gate verdict, and none may leave by way of a traceback: exit 1
    # from an uncaught exception is the code that means "a gate is below its
    # threshold", and a run that never reached the target has no gate verdict
    # to report.
    except (ValueError, OSError, TargetError, JudgeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return int(result)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
