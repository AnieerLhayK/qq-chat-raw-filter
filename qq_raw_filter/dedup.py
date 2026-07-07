"""
dedup.py — Exact and near-duplicate detection for MyBlock objects.

Exact dedup uses SHA256 of my_text. Near-dedup provides abstract interfaces
(MinHashSignature, SimHashSignature) for future implementation.
"""

from __future__ import annotations

import hashlib
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Set

from qq_raw_filter.block_builder import MyBlock

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Near-dedup abstract interfaces (placeholder for future MinHash/SimHash)
# ---------------------------------------------------------------------------

class NearDupSignature(ABC):
    """Abstract base for near-duplicate signature computation."""

    @abstractmethod
    def compute(self, text: str) -> List[int]:
        """Compute a signature for the given text."""
        ...

    @abstractmethod
    def similarity(self, a: List[int], b: List[int]) -> float:
        """Compare two signatures, returning 0.0 (different) to 1.0 (identical)."""
        ...


class MinHashSignature(NearDupSignature):
    """Placeholder: MinHash-based signature for Jaccard similarity."""

    def compute(self, text: str) -> List[int]:
        """Return an empty list — not implemented."""
        return []

    def similarity(self, a: List[int], b: List[int]) -> float:
        """Return 0.0 — not implemented."""
        return 0.0


class SimHashSignature(NearDupSignature):
    """Placeholder: SimHash-based signature for cosine similarity."""

    def compute(self, text: str) -> List[int]:
        """Return an empty list — not implemented."""
        return []

    def similarity(self, a: List[int], b: List[int]) -> float:
        """Return 0.0 — not implemented."""
        return 0.0


# ---------------------------------------------------------------------------
# Exact dedup
# ---------------------------------------------------------------------------

def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def dedup_blocks(
    blocks: List[MyBlock],
    keep_limit: int = 3,
) -> tuple[int, Dict[str, int]]:
    """Exact dedup: mark duplicate blocks as rejected.

    For each unique my_text, the first ``keep_limit`` occurrences are kept;
    subsequent ones are marked ``bucket = "rejected"`` with a dedup reason.

    Returns:
        (n_removed, hash_counter) — how many were marked as duplicates,
        and the dict of hash -> count seen
    """
    hash_counter: Dict[str, int] = {}
    removed = 0

    for block in blocks:
        h = _sha256(block.my_text)
        count = hash_counter.get(h, 0)
        if count >= keep_limit:
            block.bucket = "rejected"
            block.reasons.append(f"exact_duplicate:kept_{keep_limit}_already")
            removed += 1
        hash_counter[h] = count + 1

    return removed, hash_counter
