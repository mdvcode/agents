"""Deterministic evaluation primitives for AI Harness run artifacts."""

from .comparison import compare_reports
from .corpus import (
    CORPUS_METRICS,
    compare_corpus_to_baseline,
    corpus_dataset_fingerprint,
    corpus_scorer_fingerprint,
    evaluate_corpus,
    validate_corpus_dataset,
)
from .leaderboard import build_leaderboard
from .runner import evaluate_dataset
from .scoring import METRIC_NAMES, score_run

__all__ = [
    "METRIC_NAMES",
    "CORPUS_METRICS",
    "build_leaderboard",
    "compare_corpus_to_baseline",
    "corpus_dataset_fingerprint",
    "corpus_scorer_fingerprint",
    "compare_reports",
    "evaluate_corpus",
    "evaluate_dataset",
    "score_run",
    "validate_corpus_dataset",
]
