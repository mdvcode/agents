"""Rank scorecards while preserving evidence coverage and report identity."""

from __future__ import annotations

from typing import Any, Iterable

from .io import EvaluationInputError, utc_now


def _scorecards(report: dict[str, Any], source: str) -> Iterable[dict[str, Any]]:
    if report.get("kind") == "scorecard":
        yield {"scorecard": report, "source": source, "case": ""}
        return
    for case in report.get("cases", []):
        if isinstance(case, dict) and isinstance(case.get("scorecard"), dict):
            yield {"scorecard": case["scorecard"], "source": source, "case": str(case.get("id", ""))}


def build_leaderboard(reports: list[tuple[str, dict[str, Any]]], *, minimum_coverage: float = 0.6) -> dict[str, Any]:
    if not 0 <= minimum_coverage <= 1:
        raise EvaluationInputError("minimum_coverage must be between 0 and 1")
    entries: list[dict[str, Any]] = []
    rubric_fingerprints: set[str] = set()
    for source, report in reports:
        rubric = report.get("rubric", {})
        if isinstance(rubric, dict) and rubric.get("fingerprint"):
            rubric_fingerprints.add(str(rubric["fingerprint"]))
        for item in _scorecards(report, source):
            scorecard = item["scorecard"]
            subject = scorecard.get("subject", {})
            coverage = float(scorecard.get("coverage", 0.0) or 0.0)
            overall = scorecard.get("overall_score")
            eligible = isinstance(overall, (int, float)) and coverage >= minimum_coverage
            entries.append(
                {
                    "rank": None,
                    "eligible": eligible,
                    "label": str(subject.get("label", subject.get("run_id", "subject"))),
                    "run_id": str(subject.get("run_id", "")),
                    "case": item["case"],
                    "score": float(overall) if isinstance(overall, (int, float)) else None,
                    "coverage": coverage,
                    "variant": subject.get("variant", {}),
                    "source": source,
                }
            )
    if len(rubric_fingerprints) > 1:
        raise EvaluationInputError("leaderboard reports use different rubric fingerprints")
    entries.sort(
        key=lambda entry: (
            not entry["eligible"],
            -(entry["score"] if entry["score"] is not None else -1.0),
            -entry["coverage"],
            entry["label"],
        )
    )
    rank = 0
    for entry in entries:
        if entry["eligible"]:
            rank += 1
            entry["rank"] = rank
    return {
        "schema_version": 1,
        "kind": "evaluation_leaderboard",
        "created_at": utc_now(),
        "minimum_coverage": minimum_coverage,
        "rubric_fingerprint": next(iter(rubric_fingerprints), ""),
        "entry_count": len(entries),
        "eligible_count": rank,
        "entries": entries,
    }


def render_markdown(leaderboard: dict[str, Any]) -> str:
    lines = [
        "# Evaluation Leaderboard",
        "",
        f"Minimum coverage: {float(leaderboard['minimum_coverage']):.1%}",
        "",
        "| Rank | Label | Case | Score | Coverage | Eligible |",
        "| ---: | --- | --- | ---: | ---: | :---: |",
    ]
    for entry in leaderboard["entries"]:
        rank = str(entry["rank"]) if entry["rank"] is not None else "-"
        score = f"{entry['score']:.3f}" if entry["score"] is not None else "-"
        lines.append(
            f"| {rank} | {entry['label']} | {entry['case']} | {score} | "
            f"{entry['coverage']:.1%} | {'yes' if entry['eligible'] else 'no'} |"
        )
    return "\n".join(lines) + "\n"
