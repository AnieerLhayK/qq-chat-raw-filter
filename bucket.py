"""
bucket.py — Classify MyBlock objects into buckets based on scores and config thresholds.

Buckets:
  candidates     — High-value material for character skill extraction
  micro_style    — Short style fragments (tone words, short expressions)
  need_anonymize — Has style value but contains privacy-sensitive content
  chaos_style    — Abstract/complaint/abusive style, not fit for candidates
  rejected       — Junk, system noise, low-information content
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

from block_builder import MyBlock
from scorer import score_all

logger = logging.getLogger(__name__)


def classify_block(block: MyBlock, config: Dict[str, Any]) -> Tuple[str, List[str]]:
    """Classify a single block into a bucket.

    Returns:
        (bucket_name, list_of_reasons)
    """
    scores = score_all(block, config)
    block.scores = scores
    reasons: List[str] = []

    sc = config.get("score", {})
    rc = config.get("ratio", {})

    style = scores["style_score"]
    privacy = scores["privacy_score"]
    junk = scores["junk_score"]
    chaos = scores["chaos_score"]

    # 1. High privacy
    if privacy >= 8:
        if style >= sc.get("need_anonymize_min_style_score", 3):
            reasons.append("high_privacy_but_has_style")
            block.bucket = "need_anonymize"
            return "need_anonymize", reasons
        reasons.append("high_privacy_no_style")
        block.bucket = "rejected"
        return "rejected", reasons

    # 2. High junk
    if junk >= sc.get("junk_reject_score", 6):
        reasons.append(f"junk_score_too_high:{junk}")
        block.bucket = "rejected"
        return "rejected", reasons

    # 3. Moderate privacy
    if privacy >= sc.get("need_anonymize_min_style_score", 3):
        if style >= sc.get("need_anonymize_min_style_score", 3):
            reasons.append("medium_privacy_with_style")
            block.bucket = "need_anonymize"
            return "need_anonymize", reasons

    # 4. High chaos
    if chaos >= sc.get("chaos_separate_score", 4):
        if style >= 2 and junk < 4:
            reasons.append("chaotic_but_has_style")
            block.bucket = "chaos_style"
            return "chaos_style", reasons
        if junk >= sc.get("junk_reject_score", 6) * 0.5:
            reasons.append("chaos_and_junk")
            block.bucket = "rejected"
            return "rejected", reasons
        reasons.append("chaos_separated")
        block.bucket = "chaos_style"
        return "chaos_style", reasons

    # 5. High style
    if style >= sc.get("candidate_min_style_score", 5):
        char_count = block.metrics.get("my_char_count", 0)
        if char_count >= sc.get("candidate_min_chars_when_style_high", 30):
            reasons.append("high_style_score")
            block.bucket = "candidates"
            return "candidates", reasons
        reasons.append("short_but_high_style")
        block.bucket = "micro_style"
        return "micro_style", reasons

    # 6. Low style but some character
    if style >= sc.get("micro_style_min_style_score", 2):
        reasons.append("low_style_but_has_tone")
        block.bucket = "micro_style"
        return "micro_style", reasons

    # 7. Fallback
    reasons.append("low_style_and_high_junk_or_chaos")
    block.bucket = "rejected"
    return "rejected", reasons


def classify_all(blocks: List[MyBlock], config: Dict[str, Any]) -> Dict[str, List[MyBlock]]:
    """Classify all blocks and return a bucket->blocks mapping."""
    buckets: Dict[str, List[MyBlock]] = {
        "candidates": [],
        "micro_style": [],
        "need_anonymize": [],
        "chaos_style": [],
        "rejected": [],
    }

    for block in blocks:
        bucket, reasons = classify_block(block, config)
        block.reasons = reasons
        if bucket in buckets:
            buckets[bucket].append(block)
        else:
            logger.warning("Unknown bucket %s for block %s", bucket, block.block_id)
            buckets["rejected"].append(block)

    return buckets
