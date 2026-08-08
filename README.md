# Gauntlet

Evaluation gates for generative AI features, runnable in CI, with an evidence
pack mapped to California's published GenAI risk and procurement framework.

Gauntlet runs YAML-driven gate suites against any HTTP endpoint or Python
callable and fails the build when a gate fails. It targets a feature in its
context (prompts, retrieval, guardrails, routing), not a foundation model, and
depends on no model vendor. Tests are hermetic: the self-test target runs
locally, and nothing here makes a network call.

## Alignment, not compliance

Gauntlet is **aligned to**, not **approved by**, the State of California.
Running these gates does not make any system compliant with SIMM 5305-F, SAM
4986.9, Government Code 11549.64, or anything else. The State of California has
not reviewed or endorsed this project. The gate-to-framework mapping, and the
identifiers it deliberately omits because they could not be verified from the
source, live in [docs/california-mapping.md](docs/california-mapping.md).

## The gates

| Gate | What it enforces |
|---|---|
| **Grounding assertion** | Every factual answer carries a source identifier, and every identifier appears in the context the target reports retrieving. Uncited answers fail; identifiers are validated, never inferred. |
| **Adversarial suite** | Parameterized prompt-injection cases across system-prompt override, role manipulation, jailbreak, prompt-leak, code-execution, and Unicode/obfuscation, in English and Spanish as peers. |
| **Refusal and escalation** | Must-refuse and crisis-routing cases at a 100% pass threshold. |
| **False-positive guard** | A legitimate-request allow-list, so a gate that blocks everything cannot masquerade as safety. |
| **Golden-answer regression** | A versioned answer key with drift reporting between runs. |

## Case counts

Counts are emitted by the harness, not asserted in prose. Run `gauntlet run`
against the built-in suites to see the current totals; as of this writing the
built-in bilingual suites hold, per language:

| Gate | English | Spanish |
|---|---|---|
| grounding | 6 | 6 |
| adversarial | 12 | 12 |
| refusal | 5 | 5 |
| false_positive | 6 | 6 |
| golden | 4 | 4 |

## Self-test doctrine

A check that has never failed is not evidence of health. Gauntlet ships a
deliberately breakable grounded-RAG toy target
([`src/gauntlet/toy`](src/gauntlet/toy)) and a paired test for every gate that
injects the exact defect the gate exists to catch, then asserts the gate fails.
See [`tests/test_self_test_doctrine.py`](tests/test_self_test_doctrine.py). CI
runs these on every push.

## Usage

```sh
uv sync

# Run the built-in bilingual suites against the in-repo toy target.
uv run gauntlet run --out results.json

# Run against your own case files.
uv run gauntlet run --cases path/to/cases --out results.json

# Run against an HTTP endpoint (POST {"prompt","language"} -> response contract).
uv run gauntlet run --http-url https://your-service.example/evaluate --out results.json

# Run against a Python callable factory that returns a target.
uv run gauntlet run --callable your_package.module:make_target

# Turn results into a Markdown or JSON report.
uv run gauntlet report results.json
uv run gauntlet report results.json --format json --out report.json
```

### The target contract

A target answers a prompt in a language and reports, honestly, what it did. Over
HTTP the request body is `{"prompt": str, "language": str}` and the response body
is:

```json
{
  "text": "the answer",
  "citations": ["RB-001"],
  "context_ids": ["RB-001", "RB-002"],
  "refused": false,
  "escalated": false
}
```

The harness checks these fields; it never infers them. A Python target is any
object with a `name` attribute and an `ask(prompt, language) -> TargetResponse`
method.

## Development

```sh
make verify   # ruff format check, ruff lint, mypy strict, pytest with coverage gate
make demo     # run the gates against the toy and render a report
```

## Status and roadmap

Milestones 1 and 2 are implemented: the California mapping and package skeleton
(M1), and the five core gates plus the breakable toy and its mutation self-tests
(M2). Milestone 3 (an evidence-pack `gauntlet report` that cross-references each
gate outcome to specific SIMM 5305-F items, with run-to-run drift) and Milestone
4 (publication polish) remain. See [SCOPE.md](SCOPE.md).

## License

Apache-2.0. See [LICENSE](LICENSE).
