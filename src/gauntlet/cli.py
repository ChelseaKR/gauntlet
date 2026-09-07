"""Command-line interface: ``gauntlet run``, ``report``, ``verify``, ``sign``,
``inventory``, ``lint``, ``history``, ``compare``, ``site``, ``calibrate``.

``run`` evaluates a target against a directory of case files (or the
built-in bilingual suites) and writes a results JSON, exiting non-zero if
any gate fails. ``report`` turns one results JSON (optionally with a
baseline results JSON for whole-run drift) into the evidence pack, in
machine-readable JSON or as a human-readable document. ``inventory`` prints
the gate inventory with counts taken from the loaded suites. ``lint`` checks a
case directory statically, contacting nothing, and predicts a run the harness
would refuse to score. ``history append`` and ``history check`` keep and read an
append-only, hash-chained ledger of runs, and ``compare`` puts N results files
side by side. ``verify`` recomputes an evidence pack's own numbers and checks
them against its results file, its rendered document, and a detached
signature; ``sign`` produces that signature. ``site``
renders the documentation site from the harness: the counts are the
inventory's, and the evidence excerpts are runs made while it builds.
``calibrate`` is where a person labels a judge suite's calibration pairs and
seals them; it is the only thing that writes ``labeled_by``.

The default target is the in-repo toy, so the CLI is demonstrable with no
network and no configuration. Real targets are selected with ``--http-url``
or, for a Python callable, ``--callable path.to:factory``. The default only
applies to the zero-configuration demo: supplying ``--cases`` without a target
is a misconfiguration, not a request to evaluate a fictional city's toy
assistant, and it is refused rather than answered with a green verdict.

``run --record FILE`` writes every exchange; ``run --replay FILE`` grades that
recording and contacts nothing, so a merge gate can be deterministic and free.

Exit codes: 0 a clean run, 1 a gate below its threshold, 2 the harness itself
could not run, 3 evidence that does not reconcile, 4 the run could not be
scored.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from gauntlet.calibrate import (
    CONFIRMATION,
    Labeling,
    apply_labeling,
    changed_labels,
    describe,
    export_labels,
    interactive_session,
    parse_labeled_on,
    read_labels,
    today,
    write_calibration,
)
from gauntlet.cases import Suite, builtin_suites, load_suites
from gauntlet.evidence import build_evidence_pack, github_output_lines
from gauntlet.gates import judge_withheld_reason, run_suite, unscoreable_reason
from gauntlet.history import (
    DEFAULT_DECLINE_STREAK,
    append_run,
    check_ledger,
    compare_runs_many,
    read_ledger,
    render_check_text,
)
from gauntlet.integrity import (
    Finding,
    check_against_report,
    check_against_results,
    check_pack,
    check_signature,
    default_signature_path,
    failed,
    load_pack,
    load_signature,
    pack_sha256,
    read_key,
    render_signature,
    sign_pack,
    summary_lines,
)
from gauntlet.inventory import (
    BEGIN_MARKER,
    build_inventory,
    render_inventory_markdown,
    update_marked_block,
)
from gauntlet.judge import (
    DEFAULT_JUDGE_REGION,
    BedrockJudge,
    Judge,
    JudgeError,
    RecordingJudge,
    load_calibration,
)
from gauntlet.lint import lint_directory, render_lint_text
from gauntlet.recording import RecordingTarget, ReplayTarget, load_recording
from gauntlet.report import render_compare_markdown, render_json, render_markdown
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

# Evidence that does not reconcile. No gate said anything here, so this cannot
# be exit 1: "a gate is below its threshold" and "this document does not
# follow from its own rows" are different messages to a reviewer, and one of
# them is about the harness's own output rather than the target's.
EXIT_INTEGRITY = 3


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
    replay = getattr(args, "replay", None)
    record = getattr(args, "record", None)
    chosen = [bool(args.http_url), bool(args.callable), bool(replay)]
    if sum(chosen) > 1:
        # A replay contacts nothing, so naming a live target beside it asks for
        # two different runs at once. Silently preferring either one would put a
        # verdict about one system under the other one's name.
        raise ValueError("choose at most one of --http-url, --callable or --replay")
    if replay:
        if record:
            raise ValueError(
                "--record and --replay together would copy a recording under a new digest "
                "without contacting anything; record from a target, replay from a file"
            )
        return ReplayTarget(load_recording(Path(replay)))
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
    recorder = RecordingTarget(target, Path(args.record)) if args.record else None
    if recorder is not None:
        target = recorder
    gates = tuple(run_suite(suite, target, judge) for suite in suites)
    # Written only once every suite has answered. A run that stops partway
    # leaves no recording, for the reason it leaves no results file: half a
    # measurement replayed later reads exactly like a whole one.
    if recorder is not None:
        print(f"recorded {recorder.exchanges} exchanges to {recorder.close()}")
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
    history = None
    if args.ledger:
        history = check_ledger(read_ledger(Path(args.ledger)), args.decline_streak)
    pack = build_evidence_pack(run, baseline, history)
    rendered = render_json(pack) if args.format == "json" else render_markdown(pack)
    if args.out:
        out_path = _write(args.out, rendered)
        print(f"wrote {args.format} evidence pack to {out_path}")
    else:
        print(rendered)
    if args.github_output:
        _append_github_output(Path(args.github_output), pack)
    return 0


def _append_github_output(path: Path, pack: dict[str, object]) -> None:
    """The pack's headline counts, plus the digest of the pack's own bytes.

    ``pack-sha256`` is computed here rather than in ``github_output_lines``
    because it is a digest of the rendered JSON, and ``render_json`` is the one
    place that decides those bytes. A consumer records it beside the run and
    can later hand it to ``gauntlet verify --key-file`` to show the pack it
    holds is the one this job produced.
    """
    lines = [*github_output_lines(pack), f"pack-sha256={pack_sha256(render_json(pack))}"]
    with path.open("a", encoding="utf-8") as handle:
        for line in lines:
            handle.write(line + "\n")


def _cmd_verify(args: argparse.Namespace) -> int:
    """Check an evidence pack against itself, its sources, and its signature.

    Every check that can be run with the inputs given is run; the command does
    not stop at the first failure, because a reviewer needs the whole list.
    Exit 3 when anything failed to reconcile, 0 otherwise. An unverifiable
    check is neither: it is printed, counted separately, and never added to
    the ok tally.
    """
    pack_path = Path(args.evidence)
    pack_text, pack = load_pack(pack_path)
    findings = check_pack(pack)

    if args.results:
        baseline = load_run_dict(Path(args.baseline)) if args.baseline else None
        history = None
        if args.ledger:
            history = check_ledger(read_ledger(Path(args.ledger)), args.decline_streak)
        findings.extend(
            check_against_results(pack, load_run_dict(Path(args.results)), baseline, history)
        )
    else:
        findings.append(
            Finding(
                "rebuild",
                None,
                "no results file was given, so this pack was not compared against one: "
                "pass --results",
            )
        )

    if args.report:
        report_path = Path(args.report)
        try:
            rendered = report_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(f"cannot read the report {report_path}: {exc}") from exc
        findings.append(check_against_report(pack, rendered))
    else:
        findings.append(Finding("report", None, "no rendered document was given: pass --report"))

    findings.extend(_signature_findings(args, pack_path, pack_text))

    for line in summary_lines(findings):
        print(line)
    if failed(findings):
        print(
            f"error: {pack_path} does not reconcile; see the FAILED lines above",
            file=sys.stderr,
        )
        return EXIT_INTEGRITY
    return 0


def _signature_findings(args: argparse.Namespace, pack_path: Path, pack_text: str) -> list[Finding]:
    """The signature check, or the reason it was not performed.

    Absence of a key is reported as unverifiable rather than skipped. A pack
    whose signature nobody looked at has not been shown to be authentic, and a
    silent skip is exactly how that becomes indistinguishable from a pass.
    """
    if not args.key_file:
        return [
            Finding(
                "signature",
                None,
                "no key was given, so authorship was not checked: pass --key-file",
            )
        ]
    signature_path = Path(args.signature) if args.signature else default_signature_path(pack_path)
    return check_signature(pack_text, load_signature(signature_path), read_key(Path(args.key_file)))


def _cmd_sign(args: argparse.Namespace) -> int:
    """Write a detached HMAC-SHA256 signature for one evidence pack."""
    pack_path = Path(args.evidence)
    pack_text, _ = load_pack(pack_path)
    document = sign_pack(pack_text, read_key(Path(args.key_file)), args.signed_by or "")
    out_path = Path(args.out) if args.out else default_signature_path(pack_path)
    _write(str(out_path), render_signature(document))
    print(f"wrote {document['algorithm']} signature for {pack_path} to {out_path}")
    print(f"pack-sha256={document['pack_sha256']}")
    return 0


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


def _cmd_lint(args: argparse.Namespace) -> int:
    """Check a case directory statically, without contacting anything.

    Exit 1 when anything is wrong with the suites, which is the same code a
    failed gate uses: in both cases the answer is "this does not pass, and the
    fix is in the repository". Exit 2 stays for lint itself not completing,
    such as a case file that cannot be read. Warnings do not decide the exit
    code; they are reported and the command still passes.
    """
    report = lint_directory(Path(args.cases))
    if args.format == "json":
        print(json.dumps(report.to_dict(), indent=2, sort_keys=False), end="\n")
    else:
        print(render_lint_text(report), end="")
    return 0 if report.ok else 1


def _cmd_history(args: argparse.Namespace) -> int:
    """Append a run to the ledger, or read what the ledger shows.

    ``check`` exits 1 on a finding, the same code a failed gate uses: in both
    cases the answer is "this does not pass, and the fix is in the repository".
    A ledger whose chain is broken raises instead, and leaves by way of exit 2,
    because a tampered record is not a verdict about the target.
    """
    ledger = Path(args.ledger)
    if args.history_command == "append":
        entry = append_run(ledger, load_run_dict(Path(args.results)))
        print(f"appended run {entry['results_digest']} to {ledger}")
        return 0
    report = check_ledger(read_ledger(ledger), args.decline_streak)
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=False))
    else:
        print(render_check_text(report), end="")
    return 0 if report["ok"] else 1


def _cmd_compare(args: argparse.Namespace) -> int:
    matrix = compare_runs_many([load_run_dict(Path(path)) for path in args.results])
    rendered = (
        json.dumps(matrix, indent=2, sort_keys=False) + "\n"
        if args.format == "json"
        else render_compare_markdown(matrix)
    )
    if args.out:
        print(f"wrote the comparison to {_write(args.out, rendered)}")
    else:
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


def _cmd_calibrate(args: argparse.Namespace) -> int:
    """A person labels the calibration pairs and seals them; see ``gauntlet.calibrate``.

    ``labeled_by`` is written here and nowhere else, and only from what the
    reviewer typed or passed: never from the environment, git, or a default.
    """
    path = Path(args.calibration)
    calibration_set = load_calibration(path)
    if args.check:
        ok, sentence = describe(calibration_set)
        print(sentence)
        return 0 if ok else 1
    if args.export:
        count = export_labels(calibration_set, Path(args.export))
        print(f"wrote {count} pairs to {args.export}; fill in 'verdict' on each line, then")
        print(
            f'  gauntlet calibrate {path} --labels {args.export} --labeled-by "Your Name" '
            "--i-am-a-human-reviewer"
        )
        return 0
    labeled_on = parse_labeled_on(args.labeled_on) if args.labeled_on else today()
    if args.labels:
        if not args.labeled_by or not args.labeled_by.strip():
            raise ValueError("--labels needs --labeled-by: the reviewer's name is never filled in")
        if not args.i_am_a_human_reviewer:
            raise ValueError(
                "--labels needs --i-am-a-human-reviewer: the labels are recorded as a "
                f"person's, and the flag is that person saying so ({CONFIRMATION!r})"
            )
        labeling: Labeling | None = Labeling(
            verdicts=read_labels(Path(args.labels), calibration_set),
            labeled_by=args.labeled_by,
        )
    else:
        if args.labeled_by or args.i_am_a_human_reviewer:
            raise ValueError(
                "--labeled-by and --i-am-a-human-reviewer go with --labels; without a labels "
                "file the reviewer is asked for both during the session"
            )
        labeling = interactive_session(calibration_set, ask=input, say=print)
    if labeling is None:
        return 1
    sealed = apply_labeling(calibration_set, labeling, labeled_on)
    write_calibration(sealed, path)
    changed = changed_labels(calibration_set, sealed)
    print(
        f"wrote {path}: {len(sealed.pairs)} pairs labeled by {sealed.labeled_by} on "
        f"{sealed.labeled_on}, {len(changed)} changed from the draft"
        + (f" ({', '.join(changed)})" if changed else "")
    )
    print(f"seal {sealed.seal}: tamper evidence, not authentication")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gauntlet", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    _add_run_parser(sub)
    _add_report_parser(sub)
    _add_verify_parser(sub)
    _add_sign_parser(sub)
    _add_inventory_parser(sub)
    _add_lint_parser(sub)
    _add_history_parser(sub)
    _add_compare_parser(sub)
    _add_site_parser(sub)
    _add_calibrate_parser(sub)
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
        "--record",
        help="write every request and response to this JSON Lines file, so a later run can "
        "be graded from the recording instead of the target",
    )
    run_parser.add_argument(
        "--replay",
        help="grade this recording instead of contacting a target; no socket is opened, and "
        "the pack's provenance names the recording and its sha256",
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
        "--ledger",
        help="a run ledger from 'gauntlet history append'; adds a 'Since the last N runs' "
        "section. Without it the pack is byte-identical to one rendered without this flag",
    )
    report_parser.add_argument(
        "--decline-streak",
        type=int,
        default=DEFAULT_DECLINE_STREAK,
        help=f"how many consecutive declining runs the ledger section reports "
        f"(default: {DEFAULT_DECLINE_STREAK})",
    )
    report_parser.add_argument(
        "--github-output",
        help="append GitHub Actions 'name=value' output lines to this file",
    )
    report_parser.set_defaults(func=_cmd_report)


def _add_verify_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    verify_parser = sub.add_parser(
        "verify", help="check an evidence pack against itself, its results, and its signature"
    )
    verify_parser.add_argument("evidence", help="path to an evidence pack JSON")
    verify_parser.add_argument(
        "--results", help="the results JSON the pack was built from, to rebuild and compare"
    )
    verify_parser.add_argument(
        "--baseline", help="the baseline results JSON, so the drift block can be re-derived"
    )
    verify_parser.add_argument(
        "--ledger", help="the run ledger, so the history block can be re-derived"
    )
    verify_parser.add_argument(
        "--decline-streak",
        type=int,
        default=DEFAULT_DECLINE_STREAK,
        help=f"the streak the ledger section was rendered with (default: {DEFAULT_DECLINE_STREAK})",
    )
    verify_parser.add_argument(
        "--report", help="the rendered Markdown document, compared byte for byte to a re-render"
    )
    verify_parser.add_argument(
        "--key-file", help="the shared secret the detached signature was made with"
    )
    verify_parser.add_argument(
        "--signature",
        help="path to the detached signature (default: the pack's name with a .sig.json suffix)",
    )
    verify_parser.set_defaults(func=_cmd_verify)


def _add_sign_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    sign_parser = sub.add_parser(
        "sign", help="write a detached HMAC-SHA256 signature for an evidence pack"
    )
    sign_parser.add_argument("evidence", help="path to an evidence pack JSON")
    sign_parser.add_argument(
        "--key-file",
        required=True,
        help="file holding the shared secret; generated with e.g. "
        "'openssl rand -hex 32 > gauntlet.key'",
    )
    sign_parser.add_argument(
        "--signed-by",
        default="",
        help="the name recorded in, and authenticated by, the signature",
    )
    sign_parser.add_argument(
        "--out",
        help="where to write the signature (default: the pack's name with a .sig.json suffix)",
    )
    sign_parser.set_defaults(func=_cmd_sign)


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


def _add_lint_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    lint_parser = sub.add_parser(
        "lint",
        help="check a case directory statically, without contacting a target",
    )
    lint_parser.add_argument("cases", help="directory of *.yaml case files")
    lint_parser.add_argument("--format", choices=("text", "json"), default="text")
    lint_parser.set_defaults(func=_cmd_lint)


def _add_history_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    history_parser = sub.add_parser(
        "history", help="keep and read an append-only, hash-chained ledger of runs"
    )
    history_sub = history_parser.add_subparsers(dest="history_command", required=True)
    append_parser = history_sub.add_parser("append", help="append one results JSON to a ledger")
    append_parser.add_argument("--results", required=True, help="path to a results JSON")
    append_parser.add_argument("--ledger", required=True, help="path to the ledger JSON Lines file")
    check_parser = history_sub.add_parser("check", help="report what a ledger's runs show")
    check_parser.add_argument("--ledger", required=True, help="path to the ledger JSON Lines file")
    check_parser.add_argument(
        "--decline-streak",
        type=int,
        default=DEFAULT_DECLINE_STREAK,
        help=f"how many consecutive declining runs make a finding "
        f"(default: {DEFAULT_DECLINE_STREAK})",
    )
    check_parser.add_argument("--format", choices=("text", "json"), default="text")
    history_parser.set_defaults(func=_cmd_history)


def _add_compare_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    compare_parser = sub.add_parser(
        "compare", help="put N results files side by side, per gate and per language"
    )
    compare_parser.add_argument("results", nargs="+", help="two or more results JSON paths")
    compare_parser.add_argument("--format", choices=("md", "json"), default="md")
    compare_parser.add_argument("--out", help="write the comparison to this path")
    compare_parser.set_defaults(func=_cmd_compare)


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


def _add_calibrate_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    calibrate_parser = sub.add_parser(
        "calibrate",
        help="a person labels a judge suite's calibration pairs and seals them",
        description=(
            "Walks a reviewer through every pair in a calibration set, records their "
            "verdicts, their name, and the date, and writes a seal over the labels. "
            "With --labels, imports verdicts a reviewer filled in elsewhere instead. "
            "labeled_by is never filled in from the environment or a default."
        ),
    )
    calibrate_parser.add_argument("calibration", help="path to the calibration YAML to label")
    calibrate_parser.add_argument(
        "--labels",
        help="JSON Lines file of {id, verdict} rows to import instead of an interactive session",
    )
    calibrate_parser.add_argument(
        "--labeled-by", help="the reviewer's name, recorded as labeled_by (with --labels)"
    )
    calibrate_parser.add_argument(
        "--i-am-a-human-reviewer",
        action="store_true",
        help="the reviewer's confirmation that the imported labels are a person's (with --labels)",
    )
    calibrate_parser.add_argument(
        "--labeled-on", help="the date the labels were made (default: today, UTC)"
    )
    calibrate_parser.add_argument(
        "--export", help="write the pairs as JSON Lines for labeling elsewhere, and stop"
    )
    calibrate_parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "report every reason the judge gate would refuse this set -- unsigned, "
            "unsealed, resealed, too few pairs, one verdict only; exit 1 if any"
        ),
    )
    calibrate_parser.set_defaults(func=_cmd_calibrate)


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
