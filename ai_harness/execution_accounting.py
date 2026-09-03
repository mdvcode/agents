"""Shared accounting rules for executed, recovered, and replayed role checkpoints."""

from __future__ import annotations

from typing import Any, Mapping, Sequence


NON_EXECUTION_ROLES = {"approval-gate"}


def safe_int(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def incremental_tokens(result: Mapping[str, Any]) -> int:
    """Return incremental usage, excluding input served from cache."""

    input_tokens = result.get("input_tokens")
    cached_input_tokens = result.get("cached_input_tokens")
    output_tokens = result.get("output_tokens")
    if all(
        isinstance(value, int) and not isinstance(value, bool)
        for value in (input_tokens, cached_input_tokens, output_tokens)
    ):
        return max(int(input_tokens) - int(cached_input_tokens), 0) + int(output_tokens)
    return safe_int(result.get("tokens_used", 0))


def terminal_selection_without_runtime(checkpoint: Mapping[str, Any]) -> bool:
    result = checkpoint.get("result", {})
    profile = checkpoint.get("execution_profile", {})
    return bool(
        isinstance(result, Mapping)
        and result.get("status") == "awaiting_approval"
        and safe_int(result.get("tokens_used", 0)) == 0
        and isinstance(profile, Mapping)
        and profile.get("terminal_action") == "human_or_dead_letter"
    )


def completed_checkpoint_replay(
    checkpoint: Mapping[str, Any], prior: Sequence[Mapping[str, Any]]
) -> bool:
    provenance = checkpoint.get("cache_provenance")
    if provenance == "completed_checkpoint_replay" or checkpoint.get("cached_result_replay") is True:
        return True
    if provenance == "pending_output":
        return False
    result = checkpoint.get("result", {})
    if not (
        checkpoint.get("llm_invoked") is False
        and not checkpoint.get("execution_profile")
        and isinstance(result, Mapping)
        and incremental_tokens(result) > 0
    ):
        return False
    # Backward-compatible reconciliation for records written before explicit
    # cache provenance: a byte-for-byte repeated completed result is route-only.
    return any(
        previous.get("role") == checkpoint.get("role")
        and previous.get("result") == result
        for previous in prior
    )


def accounted_checkpoints(roles: Any) -> list[dict[str, Any]]:
    if not isinstance(roles, list):
        return []
    accounted: list[dict[str, Any]] = []
    prior: list[Mapping[str, Any]] = []
    for checkpoint in roles:
        if not isinstance(checkpoint, dict):
            continue
        if (
            checkpoint.get("role") not in NON_EXECUTION_ROLES
            and not terminal_selection_without_runtime(checkpoint)
            and not completed_checkpoint_replay(checkpoint, prior)
        ):
            accounted.append(checkpoint)
        prior.append(checkpoint)
    return accounted


def accounted_role_count(roles: Any) -> int:
    return len(accounted_checkpoints(roles))


def accounted_tokens_used(roles: Any) -> int:
    return sum(
        incremental_tokens(result)
        for checkpoint in accounted_checkpoints(roles)
        if isinstance((result := checkpoint.get("result", {})), Mapping)
    )


def role_entry_invoked_model(checkpoint: Mapping[str, Any]) -> bool:
    return bool(
        checkpoint.get("llm_invoked") is True
        and not terminal_selection_without_runtime(checkpoint)
    )
