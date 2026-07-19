"""MemPalace lifecycle contract.

This milestone intentionally defines memory management without choosing storage,
learning, summarization models, or long-term retention behavior.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class MemoryRecord:
    id: str
    content: str
    metadata: Mapping[str, str]


class MemoryManager(ABC):
    """Storage-neutral MemPalace lifecycle boundary."""

    @abstractmethod
    def remember(self, content: str, metadata: Mapping[str, str]) -> MemoryRecord:
        """Create a memory candidate."""

    @abstractmethod
    def forget(self, memory_id: str) -> None:
        """Remove a memory from active and archived storage."""

    @abstractmethod
    def promote(self, memory_id: str) -> MemoryRecord:
        """Promote a memory into a more durable tier."""

    @abstractmethod
    def archive(self, memory_id: str) -> MemoryRecord:
        """Move a memory out of the active retrieval set."""

    @abstractmethod
    def summarize(self, memory_ids: tuple[str, ...]) -> MemoryRecord:
        """Create a summary candidate from existing memories."""

    @abstractmethod
    def retrieve(self, query: str, *, limit: int = 6) -> tuple[MemoryRecord, ...]:
        """Retrieve memory records without defining a ranking implementation."""
