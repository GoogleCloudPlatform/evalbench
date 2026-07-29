"""Aggregate per-scorer sub-scores into a dataset quality score, grade, and rollups.

Pure functions, no I/O. The dataset quality score is a weighted average over only
the *applicable* scorers, so a scorer that dropped out (empty dataset, metric not
applicable to this product) neither contributes nor inflates the denominator.
Category rollups apply the same weight-normalized average within each category.
"""

from collections import defaultdict
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


def fraction_score(hits: int, n: int, target_fraction: float) -> int:
    """Score a share against a target: 100 once ``hits/n`` reaches the target."""
    denom = target_fraction * n
    if denom <= 0:
        return 0
    return round(min(100.0, hits / denom * 100))


def _is_graded(metric: ScoredMetric) -> bool:
    return metric.applicable and metric.score is not None


def _weighted_average(metrics: list[ScoredMetric]) -> float | None:
    """Weighted mean of numeric scores; None when no positive weight applies."""
    total_weight = sum(m.weight for m in metrics)
    if total_weight <= 0:
        return None
    return sum(m.score * m.weight for m in metrics) / total_weight


def compute_grade(metrics: list[ScoredMetric]) -> dict:
    """Roll metrics up into a dataset quality score, letter grade, and category scores.

    ``dataset_quality_score`` and ``letter_grade`` are None when nothing applies
    (every scorer dropped out), so an ungraded dataset stays distinguishable from
    a genuine F. ``category_scores`` is weight-normalized over applicable metrics.

    Because the score is normalized over only the metrics that applied, it is
    reported alongside ``graded_weight``/``total_weight`` and the names of the
    metrics left out, so a score over a partial rubric can't be mistaken for a
    score over the whole one.
    """
    applicable = [m for m in metrics if _is_graded(m)]

    dataset_quality_score = _weighted_average(applicable)
    if dataset_quality_score is not None:
        # Round before grading, so the reported number and its letter can never
        # disagree (an unrounded 59.7 shown as 60 would otherwise read as a D).
        dataset_quality_score = round(dataset_quality_score)

    by_category: dict[str, list[ScoredMetric]] = defaultdict(list)
    for metric in applicable:
        by_category[metric.category].append(metric)
    category_scores = {}
    for category, members in by_category.items():
        avg = _weighted_average(members)
        if avg is not None:
            category_scores[category] = round(avg)

    return {
        "dataset_quality_score": dataset_quality_score,
        "letter_grade": (
            letter_grade(dataset_quality_score)
            if dataset_quality_score is not None
            else None
        ),
        "category_scores": category_scores,
        "graded_weight": sum(m.weight for m in applicable),
        "total_weight": sum(m.weight for m in metrics),
        "excluded_scorers": [m.name for m in metrics if not _is_graded(m)],
    }
