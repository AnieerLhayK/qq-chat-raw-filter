"""
bucket.py — Classify MyBlock objects into buckets based on scores and config thresholds.

Buckets:
  candidates     — High-value material for character skill extraction
  micro_style    — Short style fragments (tone words, short expressions)
  need_anonymize — Has style value but contains privacy-sensitive content
  chaos_style    — Abstract/complaint/abusive style, not fit for candidates
  debatable      — Uncertain cases: has style but fails a quality gate softly
  rejected       — Junk, system noise, low-information content
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

from block_builder import MyBlock
from scorer import score_all

logger = logging.getLogger(__name__)


SCORE_KEYS = frozenset({"style_score", "privacy_score", "junk_score", "chaos_score"})


def classify_block(block: MyBlock, config: Dict[str, Any]) -> Tuple[str, List[str]]:
    """Classify a single block into a bucket.

    Uses pre-existing ``block.scores`` if all four keys are present
    (set by the pipeline's ``score_blocks`` stage); otherwise computes
    scores on the fly.

    Returns:
        (bucket_name, list_of_reasons)
    """
    if not SCORE_KEYS.issubset(block.scores):
        block.scores = score_all(block, config)
    reasons: List[str] = []

    sc = config.get("score", {})
    rc = config.get("ratio", {})

    style = block.scores["style_score"]
    privacy = block.scores["privacy_score"]
    junk = block.scores["junk_score"]
    chaos = block.scores["chaos_score"]

    # 1. High privacy (note: pre-classified blocks from dedup/filter stages
    #    are handled by classify_all() directly without calling classify_block)
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
        reasons.append(f"junk_score_too_higher:{junk}")
        block.bucket = "rejected"
        return "rejected", reasons

    # 3. Moderate privacy — uses explicit privacy threshold, not a style threshold
    privacy_medium = sc.get("privacy_medium_threshold", 5)
    if privacy >= privacy_medium and privacy < 8:
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

    # 5. High style → candidates, with quality pre-checks
    if style >= sc.get("candidate_min_style_score", 5):
        char_count = block.metrics.get("my_char_count", 0)
        if char_count >= sc.get("candidate_min_chars_when_style_high", 30):
            # Quality gates
            my_ratio = block.metrics.get("my_char_ratio", 0)

            # Collect which gates would fail
            gate_failures = []
            if chaos >= 2:
                gate_failures.append("chaos")
            if junk >= 3:
                gate_failures.append("junk")
            if my_ratio < 0.55:
                gate_failures.append("ratio")

            if gate_failures:
                # 5a. Debatable — has style but fails a gate softly
                debatable_enabled = config.get("debatable", {}).get("enable", True)
                if debatable_enabled and style >= sc.get("debatable_min_style_score", 4):
                    reasons.append(f"debatable_gate_failures:{','.join(gate_failures)}")
                    block.bucket = "debatable"
                    return "debatable", reasons
                # 5b. Otherwise reject with specific reason
                if chaos >= 2:
                    reasons.append(f"candidate_chaotic:{chaos}")
                elif junk >= 3:
                    reasons.append(f"candidate_junky:{junk}")
                else:
                    reasons.append(f"candidate_low_ratio:{my_ratio:.2f}")
                block.bucket = "rejected"
                return "rejected", reasons

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

    # 7. Fallback — decompose WHY so stats.json shows actionable signals
    fallback_parts = []
    micro_min = sc.get("micro_style_min_style_score", 2)
    if style < micro_min:
        fallback_parts.append(f"style_too_low:{style}")
    junk_threshold = sc.get("junk_reject_score", 6) * 0.4
    if junk >= junk_threshold:
        fallback_parts.append(f"elevated_junk:{junk}")
    chaos_threshold = sc.get("chaos_separate_score", 4) * 0.5
    if chaos >= chaos_threshold:
        fallback_parts.append(f"elevated_chaos:{chaos}")
    if not fallback_parts:
        fallback_parts.append(
            f"composite:style={style},junk={junk},chaos={chaos}"
        )
    reasons.append(f"rejected_fallback:{'|'.join(fallback_parts)}")
    block.bucket = "rejected"
    return "rejected", reasons


def classify_all(blocks: List[MyBlock], config: Dict[str, Any]) -> Dict[str, List[MyBlock]]:
    """Classify all blocks and return a bucket->blocks mapping."""
    buckets: Dict[str, List[MyBlock]] = {
        "candidates": [],
        "micro_style": [],
        "need_anonymize": [],
        "chaos_style": [],
        "debatable": [],
        "rejected": [],
    }

    # Cap debatable bucket
    debatable_max = config.get("score", {}).get("debatable_max_per_run", 200)
    debatable_count = 0

    for block in blocks:
        # Blocks pre-classified upstream (e.g. sub-threshold blocks from
        # extract_my_blocks already marked as 'rejected') skip scoring.
        if block.bucket:
            if block.bucket in buckets:
                buckets[block.bucket].append(block)
            else:
                buckets["rejected"].append(block)
            continue

        bucket, reasons = classify_block(block, config)
        block.reasons = reasons

        # Enforce debatable cap
        if bucket == "debatable":
            if debatable_count >= debatable_max:
                bucket = "rejected"
                reasons.append("debatable_capped")
                block.bucket = "rejected"
                buckets["rejected"].append(block)
                continue
            debatable_count += 1

        if bucket in buckets:
            buckets[bucket].append(block)
        else:
            logger.warning("Unknown bucket %s for block %s", bucket, block.block_id)
            buckets["rejected"].append(block)

    return buckets
