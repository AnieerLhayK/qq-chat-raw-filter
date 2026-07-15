"""
dedup.py — Exact and near-duplicate detection for MyBlock objects.

Exact dedup uses SHA256 of my_text. Near-dedup provides abstract interfaces
(MinHashSignature, SimHashSignature) for future implementation.
"""

from __future__ import annotations

import hashlib
import logging
import re
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


def _near_duplicate_similarity(left: str, right: str) -> float:
    """Return character-bigram Jaccard similarity after whitespace cleanup."""
    left = re.sub(r"\s+", "", left)
    right = re.sub(r"\s+", "", right)
    if left == right:
        return 1.0
    if min(len(left), len(right)) < 2:
        return 0.0
    left_grams = {left[index:index + 2] for index in range(len(left) - 1)}
    right_grams = {right[index:index + 2] for index in range(len(right) - 1)}
    return len(left_grams & right_grams) / max(1, len(left_grams | right_grams))


def dedup_blocks(
    blocks: List[MyBlock],
    keep_limit: int = 3,
    near_duplicate_enable: bool = False,
    near_duplicate_threshold: float = 0.85,
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

    retained_for_near_dedup: List[MyBlock] = []
    for block in blocks:
        h = _sha256(block.my_text)
        count = hash_counter.get(h, 0)
        if count >= keep_limit:
            block.bucket = "rejected"
            block.reasons.append(f"exact_duplicate:kept_{keep_limit}_already")
            removed += 1
        hash_counter[h] = count + 1

        if block.bucket == "rejected":
            continue
        if near_duplicate_enable:
            similarity = max(
                (_near_duplicate_similarity(block.my_text, kept.my_text)
                 for kept in retained_for_near_dedup),
                default=0.0,
            )
            if similarity >= near_duplicate_threshold:
                block.bucket = "rejected"
                block.reasons.append(f"near_duplicate:similarity={similarity:.2f}")
                removed += 1
                continue
        retained_for_near_dedup.append(block)

    return removed, hash_counter
