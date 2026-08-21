"""Conservative prompt-injection *attempt* detection — for observability, not defense.

The kit's defense against prompt injection is structural: retrieval is mandatory and the
citation guard drops any sentence not entailed by a retrieved chunk, so an injected
instruction cannot make the engine fabricate or leak a system prompt. This module does
not add to that defense — it gives operators **visibility** into attempts, so attack
patterns show up in `log-stats` (as a PII-free `injection_attempt` event keyed by query
fingerprint) and can inform corpus or proxy tuning.

Pattern-matching is deliberately conservative (high-signal markers unlikely to appear in a
genuine civic question) to keep false positives low. It is offline and deterministic.
"""

from __future__ import annotations

import re

# (category, pattern). A query can match several categories; `detect_injection` returns
# the sorted set of category names matched.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # "ignore the previous instructions", "disregard all rules", "forget your prompt"
    (
        "instruction_override",
        re.compile(
            r"\b(ignore|disregard|forget|override|bypass)\b.{0,40}"
            r"\b(previous|prior|above|earlier|all|your|the)\b.{0,20}"
            r"\b(instruction|instructions|rule|rules|prompt|prompts|context|guardrail|guardrails)\b",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    # "you are now…", "act as…", "pretend to be…", "from now on you…", "roleplay as…"
    (
        "role_play",
        re.compile(
            r"\b(you are now|act as\b|pretend(?:\s+(?:to be|you are|that))?|"
            r"from now on,? you|roleplay|role-play|jailbreak)\b",
            re.IGNORECASE,
        ),
    ),
    # Probing for or exfiltrating the hidden prompt/instructions.
    (
        "system_prompt_probe",
        re.compile(
            r"\b(system prompt|your (?:instructions|system prompt|rules|guidelines)|"
            r"(?:reveal|print|repeat|show|expose|leak) your)\b",
            re.IGNORECASE,
        ),
    ),
    # LLM control-token / template delimiters that have no place in a civic question.
    (
        "delimiter_injection",
        re.compile(r"(<\|.*?\|>|\[/?INST\]|<<\s*SYS\s*>>|\{\{.*?\}\}|</?system>)", re.IGNORECASE),
    ),
]


def detect_injection(text: str) -> list[str]:
    """Return the sorted set of prompt-injection marker categories matched in ``text``;
    an empty list means nothing matched. Conservative by design."""
    return sorted({name for name, pattern in _PATTERNS if pattern.search(text)})
