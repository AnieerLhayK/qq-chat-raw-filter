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


def _sample(blocks: List[MyBlock], sample_size: int, rng: random.Random) -> List[MyBlock]:
    """Take a random sample of up to sample_size blocks."""
    if len(blocks) <= sample_size:
        return list(blocks)
    return rng.sample(blocks, sample_size)


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
    sample_seed: int = 20260710,
) -> int:
    """Write stratified review sample files.

    Returns:
        Number of sample files written.
    """
    n_written = 0
    rng = random.Random(sample_seed)
    decision_template: List[Dict[str, str]] = []

    def write_sampled(sampled: List[MyBlock], filename: str) -> None:
        nonlocal n_written
        if not sampled:
            return
        _write_jsonl(sampled, review_dir / filename)
        decision_template.extend({
            "kind": "block",
            "key": str(block.metrics.get("review_id", "")),
            "decision": "pending",
            "reason": "",
        } for block in sampled if block.metrics.get("review_id"))
        n_written += 1

    # Candidates — stratified by style score
    candidates = bucketed.get("candidates", [])
    high_score = [b for b in candidates
                  if b.scores.get("style_score", 0) >= 8]
    low_score = [b for b in candidates
                 if 0 < b.scores.get("style_score", 0) < 8]

    for tier_name, tier_blocks in [("high_score", high_score), ("low_score", low_score)]:
        write_sampled(_sample(tier_blocks, sample_size // 2, rng), f"candidates_{tier_name}_sample.jsonl")

    # Rejected with high style score (potential misclassification)
    rejected = bucketed.get("rejected", [])
    high_style_rejected = [
        b for b in rejected
        if b.scores.get("style_score", 0) >= 4
    ]
    if high_style_rejected:
        write_sampled(_sample(high_style_rejected, sample_size, rng), "rejected_high_style_score_sample.jsonl")

    # Chaos style
    chaos = bucketed.get("chaos_style", [])
    if chaos:
        write_sampled(_sample(chaos, sample_size, rng), "chaos_style_sample.jsonl")

    # Need anonymize
    need_anon = bucketed.get("need_anonymize", [])
    if need_anon:
        write_sampled(_sample(need_anon, sample_size, rng), "need_anonymize_sample.jsonl")

    # Debatable
    debatable = bucketed.get("debatable", [])
    if debatable:
        write_sampled(_sample(debatable, sample_size, rng), "debatable_sample.jsonl")

    if decision_template:
        with (review_dir / "review_decisions.template.jsonl").open("w", encoding="utf-8") as handle:
            for item in decision_template:
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        n_written += 1

    return n_written
