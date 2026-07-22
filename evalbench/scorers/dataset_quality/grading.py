"""Aggregate per-scorer sub-scores into a dataset quality score, grade, and rollups.

Pure functions, no I/O. The dataset quality score is a weighted average over only
the *applicable* scorers, so a scorer that dropped out (empty dataset, metric not
applicable to this product) neither contributes nor inflates the denominator.
Category rollups apply the same weight-normalized average within each category.
"""

from dataclasses import dataclass

# Letter-grade bands: (inclusive lower bound, letter), highest first.
_LETTER_BANDS = (
    (90, "A"),  # production-grade
    (75, "B"),  # solid
    (60, "C"),  # usable for development
    (40, "D"),  # material rework needed
    (0, "F"),   # do not use
)


@dataclass
class ScoredMetric:
    """One scorer's outcome, flattened for aggregation."""

    name: str
    weight: float
    category: str
    score: float | None
    applicable: bool


def letter_grade(score: float) -> str:
    for threshold, letter in _LETTER_BANDS:
        if score >= threshold:
            return letter
    return "F"


def _weighted_average(metrics: list[ScoredMetric]) -> float | None:
    """Weighted mean of numeric scores; None when no positive weight applies."""
    total_weight = sum(m.weight for m in metrics)
    if total_weight <= 0:
        return None
    return sum(m.score * m.weight for m in metrics) / total_weight


def compute_grade(metrics: list[ScoredMetric]) -> dict:
    """Roll metrics up into a dataset quality score, letter grade, and category scores.

    Returns ``dataset_quality_score`` (None when nothing applies), ``letter_grade``,
    and ``category_scores`` (weight-normalized per category, applicable metrics only).
    """
    applicable = [
        m for m in metrics if m.applicable and m.score is not None
    ]

    dataset_quality_score = _weighted_average(applicable)

    category_scores: dict[str, float] = {}
    for metric in applicable:
        category_scores.setdefault(metric.category, [])
        category_scores[metric.category].append(metric)
    category_scores = {
        category: round(_weighted_average(members), 2)
        for category, members in category_scores.items()
    }

    return {
        "dataset_quality_score": (
            round(dataset_quality_score, 2)
            if dataset_quality_score is not None
            else None
        ),
        "letter_grade": (
            letter_grade(dataset_quality_score)
            if dataset_quality_score is not None
            else "F"
        ),
        "category_scores": category_scores,
    }
