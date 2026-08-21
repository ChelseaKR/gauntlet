"""Content-hashing helpers. The kit is reproducible: the same corpus + config
yields the same index and the same answers, and audit artifacts are byte-identical
across runs (no wall-clock fields). Mirrors the GovChat-Eval determinism contract.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def sha256_text(text: str) -> str:
    """Hex SHA-256 of a string (UTF-8)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_of_file(path: str | Path) -> str:
    """Hex SHA-256 of a file's bytes."""
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def canonical_json(value: Any) -> str:
    """Stable JSON: sorted keys, compact, no wall-clock — safe to hash and diff."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def fingerprint(value: Any) -> str:
    """Short content fingerprint of any JSON-serializable value."""
    return sha256_text(canonical_json(value))[:16]
