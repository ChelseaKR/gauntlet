"""In-memory cosine vector store. Pure Python (no NumPy), JSON-persistable, and
deterministic — ties in similarity are broken by chunk id so results are stable.

This is the default store: it makes the kit clone-and-run with zero infrastructure
and keeps CI offline. pgvector/OpenSearch are drop-in replacements for scale.
"""

from __future__ import annotations

import json
from pathlib import Path

from civic_rag.models import Chunk, RetrievedChunk


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


class MemoryStore:
    def __init__(self) -> None:
        self._chunks: list[Chunk] = []
        self._vectors: list[list[float]] = []

    def add(self, chunk: Chunk, vector: list[float]) -> None:
        self._chunks.append(chunk)
        self._vectors.append(vector)

    def sync(self, entries: list[tuple[Chunk, list[float]]]) -> None:
        """Replace the complete corpus without retaining chunks from an older build."""
        self._chunks = [chunk for chunk, _vector in entries]
        self._vectors = [vector for _chunk, vector in entries]

    def search(self, vector: list[float], top_k: int) -> list[RetrievedChunk]:
        # Vectors are L2-normalized at embed time, so dot product == cosine.
        scored = [
            RetrievedChunk(chunk=c, score=_dot(vector, v))
            for c, v in zip(self._chunks, self._vectors, strict=True)
        ]
        scored.sort(key=lambda rc: (-rc.score, rc.chunk.chunk_id))
        return scored[: max(0, top_k)]

    def all_chunks(self) -> list[Chunk]:
        """Every indexed chunk, for lexical (BM25) scoring in hybrid retrieval
        (see ``civic_rag.retrieve.SupportsChunks``)."""
        return list(self._chunks)

    def __len__(self) -> int:
        return len(self._chunks)

    def save(self, path: str) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "chunks": [c.model_dump() for c in self._chunks],
            "vectors": self._vectors,
        }
        p.write_text(json.dumps(payload), encoding="utf-8")

    def load(self, path: str) -> None:
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"index not found: {p} (run `civic-rag ingest` first)")
        payload = json.loads(p.read_text(encoding="utf-8"))
        self._chunks = [Chunk.model_validate(c) for c in payload["chunks"]]
        self._vectors = [list(map(float, v)) for v in payload["vectors"]]
