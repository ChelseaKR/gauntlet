"""Record every raw target response, and replay a recording instead of the network.

A live run against a metered or model-backed target is not free to repeat.
The adapters therefore write each raw response they receive to a JSON Lines
log, keyed by the request that produced it, and can be pointed back at that
log to answer the same requests without touching the target. A replayed run
reports where it came from in the provenance, so a pack built from a
recording never passes as a fresh measurement.

The log also holds the harness's own quote-check outcomes, one entry per
checked citation, written by ``quotecheck.DocumentCache``. A recording that
holds only what the target said cannot reproduce what the harness verified, so
a replay of one has to skip verification and report a verdict the live run
never reached. Recording the outcomes is what lets a replay reproduce the
grounding decision instead. Recordings made before this existed are not
back-filled: an outcome nobody measured is not evidence, and
``tests/test_real_target_packs.py`` names them and refuses a new one that
omits its outcomes.

The log holds the target's verbatim output. SECURITY.md's guidance on
evidence packs applies to it as well: treat it like production logs.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gauntlet.targets import TargetError


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


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

    def lookup(self, key: str, *, count: bool = True) -> dict[str, Any] | None:
        """The recorded entry for a key, when replaying.

        ``count`` feeds ``responses_replayed`` in the provenance, which is a
        count of target responses. A log also carries the harness's own
        quote-check outcomes, which are not responses and are counted
        separately by the checker that reads them, so that reader passes
        ``count=False``. A single counter over both would report a number no
        reader could interpret.
        """
        entry = self._replay.get(key)
        if entry is not None and count:
            self.replayed += 1
        return entry

    def record(self, key: str, entry: dict[str, Any]) -> None:
        """Append one raw response under its request key."""
        if self.write_path is None:
            return
        self.write_path.parent.mkdir(parents=True, exist_ok=True)
        stamped = {"key": key, "recorded_at": _now(), **entry}
        with self.write_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(stamped, ensure_ascii=False, sort_keys=True))
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


def replayed_or_produced(
    log: RawLog, key: str, produce: Callable[[], dict[str, Any]]
) -> dict[str, Any]:
    """The payload for ``key`` from the recording when replaying, else fresh and recorded.

    Every adapter that can be replayed needs exactly this, and the rule that
    matters is the middle branch: replaying with no entry for a key raises
    rather than falling through to the target. A replay that quietly contacted
    the system it is replaying would be a live run wearing a recording's
    provenance, and the pack would say ``replayed_from`` about a measurement
    that was made fresh.
    """
    entry = log.lookup(key)
    if entry is not None:
        payload = entry["payload"]
        if not isinstance(payload, dict):
            raise TargetError(f"replay entry for {key!r} is not an object")
        return payload
    if log.replaying:
        raise TargetError(f"replaying, and the recording has no entry for {key!r}")
    payload = produce()
    log.record(key, {"payload": payload})
    return payload
