"""Calibrated confidence tiers (high/medium/low) derived from retrieval and guard features.

Replaces the single-scalar ``low_confidence`` flag with a deterministic calibrated
confidence tier computed from features already in the pipeline:

- **best_score**: best retrieval score (cosine similarity)
- **score_margin**: gap between best and second-best retrieval score (0.0 if only one hit)
- **guard_survival**: fraction of generator candidates that pass the citation guard
  (1.0 if no candidates, 0.0 if all dropped)
- **citation_coverage**: fraction of answer sentences with valid citations (1.0 by guard)

The tiers are computed via a monotone calibration: features are combined into a
scalar via fixed weights, then bucketed via table cut-points. Deterministic and
repeatable (no randomness, round to fixed decimals).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ConfidenceFeatures:
    """Features derived from retrieval and citation guard, used to compute confidence tier."""

    best_score: float
    """Best retrieval cosine score; range [0, 1]."""
    score_margin: float
    """Gap between best and second-best score; 0.0 if only one hit. Range [0, 1]."""
    guard_survival: float
    """Fraction of generator candidates that survived the citation guard. Range [0, 1]."""
    citation_coverage: float
    """Fraction of answer sentences with citations; 1.0 by guard. Range [0, 1]."""


def features_from_retrieval(
    scores: list[float],
    candidates: int,
    kept: int,
    citation_coverage: float = 1.0,
) -> ConfidenceFeatures:
    """Build :class:`ConfidenceFeatures` from raw pipeline signals.

    Shared by the pipeline, the offline calibration fitter, and the tests so the
    feature definitions cannot drift between them.

    Args:
        scores: Retrieval scores for the retrieved chunks (any order).
        candidates: Number of sentences the generator proposed.
        kept: Number of sentences that survived the citation guard (after dedup).
        citation_coverage: Fraction of kept sentences with citations (1.0 by guard).

    Returns:
        ConfidenceFeatures with best_score, score_margin (0.0 if fewer than two
        hits), and guard_survival (1.0 when candidates == 0).
    """
    ordered = sorted(scores, reverse=True)
    best_score = ordered[0] if ordered else 0.0
    score_margin = round(ordered[0] - ordered[1], 4) if len(ordered) > 1 else 0.0
    dropped = candidates - kept
    guard_survival = 1.0 if candidates == 0 else round(1.0 - dropped / candidates, 4)
    return ConfidenceFeatures(
        best_score=best_score,
        score_margin=score_margin,
        guard_survival=guard_survival,
        citation_coverage=citation_coverage,
    )


@dataclass(frozen=True)
class CalibrationTable:
    """A committed calibration mapping features to confidence tiers."""

    # Feature weights (sum to 1.0); combined into a scalar before bucketing.
    best_score_weight: float
    score_margin_weight: float
    guard_survival_weight: float
    citation_coverage_weight: float

    # Tier cut-points: tier is "high" if combined_score >= high_cutpoint,
    # "medium" if >= medium_cutpoint, else "low".
    high_cutpoint: float
    medium_cutpoint: float

    # Per-tier empirical accuracy from the gold set (diagnostic only, not used in scoring).
    high_accuracy: float
    medium_accuracy: float
    low_accuracy: float


def score_confidence(features: ConfidenceFeatures, table: CalibrationTable) -> tuple[str, float]:
    """Compute a confidence tier and raw calibrated score.

    Args:
        features: Extracted features from retrieval and citation guard.
        table: Committed calibration table with feature weights and cut-points.

    Returns:
        A tuple (tier, score) where tier is "high", "medium", or "low", and score
        is the raw combined scalar [0, 1] before bucketing.
    """
    # Combine features into a scalar via fixed weights.
    combined_score = round(
        features.best_score * table.best_score_weight
        + features.score_margin * table.score_margin_weight
        + features.guard_survival * table.guard_survival_weight
        + features.citation_coverage * table.citation_coverage_weight,
        4,
    )

    # Bucket via cut-points.
    if combined_score >= table.high_cutpoint:
        tier = "high"
    elif combined_score >= table.medium_cutpoint:
        tier = "medium"
    else:
        tier = "low"

    return (tier, combined_score)


def load_calibration_table(path: str | Path | None = None) -> CalibrationTable:
    """Load a calibration table from a JSON file, falling back to the packaged default.

    Args:
        path: Optional path to a JSON calibration table. If None, loads the packaged
              default from civic_rag/data/confidence-calibration.json.

    Returns:
        A CalibrationTable with weights and cut-points.

    Raises:
        FileNotFoundError: If path is given but does not exist.
    """
    if path is None:
        # Fall back to the packaged default. It must live *inside* the package
        # directory (same convention as civic_rag/locales/) so it ships everywhere
        # the package does — wheels (hatchling packages the whole `civic_rag` dir)
        # and the container image (the Dockerfile COPYs `civic_rag` wholesale).
        # A repo-relative path like docs/audits/ does NOT ship (BUG-2: containerized
        # `serve` crashed at startup because docs/ is dockerignored).
        path = Path(__file__).resolve().parent / "data" / "confidence-calibration.json"
    else:
        path = Path(path)

    if not path.is_file():
        raise FileNotFoundError(f"Calibration table not found: {path}")

    data = json.loads(path.read_text(encoding="utf-8"))
    return CalibrationTable(
        best_score_weight=float(data["best_score_weight"]),
        score_margin_weight=float(data["score_margin_weight"]),
        guard_survival_weight=float(data["guard_survival_weight"]),
        citation_coverage_weight=float(data["citation_coverage_weight"]),
        high_cutpoint=float(data["high_cutpoint"]),
        medium_cutpoint=float(data["medium_cutpoint"]),
        high_accuracy=float(data["high_accuracy"]),
        medium_accuracy=float(data["medium_accuracy"]),
        low_accuracy=float(data["low_accuracy"]),
    )
