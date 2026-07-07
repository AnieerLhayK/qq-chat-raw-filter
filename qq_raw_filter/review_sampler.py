"""
review_sampler.py — Stratified review sample writer.

Produces 5 stratified sample files in the review_samples/ directory:

  candidates_high_score_sample.jsonl   — style >= 8
  candidates_low_score_sample.jsonl    — style 5-7
  rejected_high_style_score_sample.jsonl  — style >= 4 but rejected
  chaos_style_sample.jsonl             — all chaos_style blocks
  need_anonymize_sample.jsonl          — all need_anonymize blocks
"""

from __future__ import annotations

import json
import logging
import random
from pathlib import Path
from typing import Any, Dict, List

from qq_raw_filter.block_builder import MyBlock

logger = logging.getLogger(__name__)


def _sample(blocks: List[MyBlock], sample_size: int) -> List[MyBlock]:
    """Take a random sample of up to sample_size blocks."""
    if len(blocks) <= sample_size:
        return list(blocks)
    return random.sample(blocks, sample_size)


def _write_jsonl(blocks: List[MyBlock], path: Path) -> int:
    """Write blocks to a JSONL file. Returns count written."""
    with path.open("w", encoding="utf-8") as f:
        for b in blocks:
            data = b.to_dict()
            if hasattr(b, 'style_tags') and b.style_tags:
                data["style_tags"] = b.style_tags
            f.write(json.dumps(data, ensure_ascii=False) + "\n")
    return len(blocks)


def write_stratified_samples(
    bucketed: Dict[str, List[MyBlock]],
    review_dir: Path,
    sample_size: int,
) -> int:
    """Write stratified review sample files.

    Returns:
        Number of sample files written.
    """
    n_written = 0

    # Candidates — stratified by style score
    candidates = bucketed.get("candidates", [])
    high_score = [b for b in candidates
                  if b.scores.get("style_score", 0) >= 8]
    low_score = [b for b in candidates
                 if 0 < b.scores.get("style_score", 0) < 8]

    for tier_name, tier_blocks in [("high_score", high_score), ("low_score", low_score)]:
        sampled = _sample(tier_blocks, sample_size // 2)
        if sampled:
            _write_jsonl(sampled, review_dir / f"candidates_{tier_name}_sample.jsonl")
            n_written += 1

    # Rejected with high style score (potential misclassification)
    rejected = bucketed.get("rejected", [])
    high_style_rejected = [
        b for b in rejected
        if b.scores.get("style_score", 0) >= 4
    ]
    if high_style_rejected:
        sampled = _sample(high_style_rejected, sample_size)
        _write_jsonl(sampled, review_dir / "rejected_high_style_score_sample.jsonl")
        n_written += 1

    # Chaos style
    chaos = bucketed.get("chaos_style", [])
    if chaos:
        sampled = _sample(chaos, sample_size)
        _write_jsonl(sampled, review_dir / "chaos_style_sample.jsonl")
        n_written += 1

    # Need anonymize
    need_anon = bucketed.get("need_anonymize", [])
    if need_anon:
        sampled = _sample(need_anon, sample_size)
        _write_jsonl(sampled, review_dir / "need_anonymize_sample.jsonl")
        n_written += 1

    # Debatable
    debatable = bucketed.get("debatable", [])
    if debatable:
        sampled = _sample(debatable, sample_size)
        _write_jsonl(sampled, review_dir / "debatable_sample.jsonl")
        n_written += 1

    return n_written
