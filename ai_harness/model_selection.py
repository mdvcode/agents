"""Validate an explicit user model preference without selecting providers or roles."""

from __future__ import annotations

import re
from typing import Any


class ModelSelectionError(ValueError):
    """A requested model is malformed or unavailable to the current runtime."""


def normalize_model_override(value: Any = "") -> str:
    if not isinstance(value, str) or (value and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", value)):
        raise ModelSelectionError("model_override must be an available model identifier or empty for role profiles")
    return value


def require_available_model(value: Any, catalog: dict[str, Any]) -> dict[str, Any]:
    selected = normalize_model_override(value)
    if catalog.get("status") != "available":
        raise ModelSelectionError(str(catalog.get("message") or "Model availability could not be checked; retry discovery"))
    matches = [
        item for item in catalog.get("models", [])
        if isinstance(item, dict) and item.get("id") == selected
    ]
    if not selected or len(matches) != 1:
        raise ModelSelectionError(f"Model {selected!r} is not available in the current runtime catalog; refresh the model list")
    return matches[0]


def resumed_model_override(requested: Any, workflow: dict[str, Any]) -> str:
    requested = normalize_model_override(requested)
    saved = normalize_model_override(workflow.get("model_override", ""))
    if requested and requested != saved:
        raise ModelSelectionError("model_override changed since this run started; resume keeps the original model")
    return saved


def apply_model_override(settings: dict[str, Any], selected: Any) -> dict[str, Any]:
    model = normalize_model_override(selected)
    if not model:
        return settings
    return {**settings, "model": model, "model_override": model,
            "profile_reason": f"{settings.get('profile_reason', '')}; explicit user model choice"}
