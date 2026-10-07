"""Read-only model discovery through the authenticated official Codex SDK."""

from __future__ import annotations

from typing import Any


def enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def models_for_client(codex: Any) -> dict[str, Any]:
    response = codex.models(include_hidden=False)
    # The pinned high-level SDK has no cursor argument. Never present a partial catalog.
    if getattr(response, "next_cursor", None):
        return {
            "status": "unavailable", "provider": "codex-sdk", "models": [],
            "message": "The runtime returned a paginated model catalog that this SDK cannot fully read",
        }
    models = []
    for model in response.data:
        if model.hidden:
            continue
        modalities = getattr(model, "input_modalities", None)
        models.append({
            "id": model.model,
            "display_name": model.display_name,
            "description": model.description,
            "supported_reasoning_efforts": [enum_value(option.reasoning_effort) for option in model.supported_reasoning_efforts],
            "default_reasoning_effort": enum_value(model.default_reasoning_effort),
            "input_modalities": [enum_value(value) for value in (["text", "image"] if modalities is None else modalities)],
            "is_default": bool(model.is_default),
        })
    return {"status": "available", "provider": "codex-sdk", "models": models, "message": "Visible models reported by this SDK client for the current ChatGPT account; execution rechecks availability"}
