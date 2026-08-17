"""Canonical security-finding scope for run-bound human acceptance."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def security_finding_ids(security: dict[str, Any]) -> list[str]:
    blocker_ids = security.get("blocker_ids", [])
    if isinstance(blocker_ids, list):
        values = {str(item).strip() for item in blocker_ids if str(item).strip()}
    else:
        values = set()
    findings = security.get("findings", [])
    if isinstance(findings, list):
        values.update(
            str(finding.get("id", "")).strip()
            for finding in findings
            if isinstance(finding, dict) and str(finding.get("id", "")).strip()
        )
    return sorted(values)


def security_fingerprint(security: dict[str, Any]) -> str:
    findings = security.get("findings", [])
    normalized_findings = sorted(
        (
            {
                "id": str(finding.get("id", "")),
                "severity": str(finding.get("severity", "")),
                "status": str(finding.get("status", "")),
                "category": str(finding.get("category", "")),
                "scope": str(finding.get("scope", "")),
            }
            for finding in findings
            if isinstance(finding, dict)
        ),
        key=lambda finding: (finding["id"], finding["severity"], finding["category"]),
    ) if isinstance(findings, list) else []
    payload = {
        "finding_ids": security_finding_ids(security),
        "highest_severity": str(security.get("highest_severity", "")),
        "findings": normalized_findings,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def security_scope(security: dict[str, Any]) -> dict[str, Any]:
    return {
        "finding_ids": security_finding_ids(security),
        "security_fingerprint": security_fingerprint(security),
    }


def scope_accepts_security(scope: dict[str, Any], security: dict[str, Any]) -> bool:
    """Match a grant to the exact finding set; empty legacy scope stays compatible."""

    scoped_ids = scope.get("finding_ids")
    scoped_fingerprint = str(scope.get("security_fingerprint", ""))
    if scoped_ids is None and not scoped_fingerprint:
        return True
    if not isinstance(scoped_ids, list) or not scoped_fingerprint:
        return False
    return (
        sorted({str(item) for item in scoped_ids}) == security_finding_ids(security)
        and scoped_fingerprint == security_fingerprint(security)
    )


__all__ = [
    "scope_accepts_security",
    "security_finding_ids",
    "security_fingerprint",
    "security_scope",
]
