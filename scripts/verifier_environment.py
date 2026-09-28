"""Conservative classification of verifier environment limitations."""

from __future__ import annotations

import re
from typing import Any


ENVIRONMENT_LIMIT_PATTERNS = (
    r"\bcommand not found\b",
    r"\bmissing dependenc(?:y|ies)\b",
    r"\bdependenc(?:y|ies)\b.*\b(?:missing|unavailable|not installed)\b",
    r"\b(?:browser|playwright)\b.*\b(?:unavailable|missing|not installed|cannot (?:run|launch|start)|could not (?:run|launch|start|verify))\b",
    r"\b(?:unavailable|missing|not installed)\b.*\b(?:browser|playwright)\b",
    r"\bread-only\b.*\b(?:runtime|environment|workspace|file ?system)\b",
    r"\b(?:runtime|environment)\b.*\b(?:capability|dependency|tool)\b.*\b(?:unavailable|missing|not present)\b",
    r"\bverification\b.*\bdid not complete\b.*\b(?:runtime|environment|dependency|tool)\b",
)


def _environmental_blocker(value: str) -> bool:
    normalized = " ".join(value.lower().split())
    return any(re.search(pattern, normalized) for pattern in ENVIRONMENT_LIMIT_PATTERNS)


def verifier_artifact_unavailable(verifier: dict[str, Any]) -> bool:
    """Fail closed unless an unavailable or wholly environmental failure is explicit."""

    verdict = str(verifier.get("verdict", "")).lower()
    if verdict == "unavailable":
        return True
    if verdict != "broken":
        return False
    blockers = [
        str(item).lower()
        for item in verifier.get("blockers", [])
        if isinstance(item, (str, int)) and str(item).strip()
    ]
    return bool(blockers) and all(_environmental_blocker(blocker) for blocker in blockers)
