"""Record every raw target response, and replay a recording instead of the network.

A live run against a metered or model-backed target is not free to repeat.
The adapters therefore write each raw response they receive to a JSON Lines
log, keyed by the request that produced it, and can be pointed back at that
log to answer the same requests without touching the target. A replayed run
reports where it came from in the provenance, so a pack built from a
recording never passes as a fresh measurement.

The log holds the target's verbatim output. SECURITY.md's guidance on
evidence packs applies to it as well: treat it like production logs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class RawLog:
    write_path: Path | None = None
    replay_path: Path | None = None

    def __post_init__(self) -> None:
        self._replay: dict[str, dict[str, Any]] = {}
        self.replayed = 0
        self.recorded = 0
        if self.replay_path is not None:
            for line in self.replay_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                entry = json.loads(line)
                self._replay[str(entry["key"])] = entry

    @property
    def replaying(self) -> bool:
        return self.replay_path is not None

    def lookup(self, key: str) -> dict[str, Any] | None:
        """The recorded entry for a request, when replaying."""
        entry = self._replay.get(key)
        if entry is not None:
            self.replayed += 1
        return entry

    def record(self, key: str, entry: dict[str, Any]) -> None:
        """Append one raw response under its request key."""
        if self.write_path is None:
            return
        self.write_path.parent.mkdir(parents=True, exist_ok=True)
        with self.write_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"key": key, **entry}, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
        self.recorded += 1

    def provenance(self) -> dict[str, str]:
        out: dict[str, str] = {}
        if self.replay_path is not None:
            out["replayed_from"] = str(self.replay_path)
            out["responses_replayed"] = str(self.replayed)
        if self.write_path is not None:
            out["raw_log"] = str(self.write_path)
        return out
