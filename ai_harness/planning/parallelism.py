"""Safety checks and scheduling helpers for execution-plan parallel groups."""

from __future__ import annotations

from collections.abc import Iterable

from .execution_plan import ExecutionPlan


class ParallelismError(ValueError):
    """Raised when a plan attempts unsafe same-worktree concurrency."""


def ready_nodes(plan: ExecutionPlan, completed: Iterable[str]) -> tuple[str, ...]:
    completed_ids = set(completed)
    return tuple(
        node.id
        for node in plan.nodes
        if node.id not in completed_ids and set(node.dependencies) <= completed_ids
    )


def validate_parallel_groups(plan: ExecutionPlan) -> None:
    seen: set[str] = set()
    for group in plan.parallel_groups:
        if len(group) < 2:
            raise ParallelismError("parallel groups must contain at least two nodes")
        group_ids = set(group)
        if len(group_ids) != len(group):
            raise ParallelismError("parallel group nodes must be unique")
        if seen & group_ids:
            raise ParallelismError("a node cannot belong to multiple parallel groups")
        seen.update(group_ids)
        nodes = [plan.node(node_id) for node_id in group]
        if any(node is None for node in nodes):
            raise ParallelismError("parallel groups must reference known nodes")
        if any(not node.read_only for node in nodes if node is not None):
            raise ParallelismError("write nodes cannot execute concurrently in one worktree")
        if any(set(node.dependencies) & group_ids for node in nodes if node is not None):
            raise ParallelismError("parallel nodes cannot depend on each other")
        dependency_sets = {node.dependencies for node in nodes if node is not None}
        if len(dependency_sets) != 1:
            raise ParallelismError("parallel nodes must share the same dependency frontier")
