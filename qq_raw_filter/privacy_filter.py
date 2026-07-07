"""
privacy_filter.py — Privacy mode and mask-list application for MyBlock objects.

Supports three modes:
  - strict:   privacy > 3 → reject outright, privacy > 1 → flag
  - balanced: current behavior (privacy >= 8 → need_anonymize if style OK)
  - recall:   privacy >= 5 → need_anonymize, privacy >= 10 → reject

Also applies private_names / private_places masking (delegated to filter_applier).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from qq_raw_filter.block_builder import MyBlock
from qq_raw_filter.filter_applier import apply_masklist

logger = logging.getLogger(__name__)


def filter_privacy_blocks(blocks: List[MyBlock], config: Dict[str, Any]) -> int:
    """Apply privacy mode adjustments to blocks.

    In 'balanced' mode, no change — the existing bucket.py logic handles it.
    In 'strict' mode, blocks with elevated privacy scores (but below the old
    threshold) are pre-emptively rejected.
    In 'recall' mode, the need_anonymize threshold is lowered.

    Also applies private_names and private_places masking.

    Returns:
        Number of masking operations performed.
    """
    privacy_cfg = config.get("privacy", {})
    mode = privacy_cfg.get("mode", "balanced")
    f_cfg = config.get("filter", {})

    n_masked = 0

    # Apply private_names and private_places masking first
    priv_names = f_cfg.get("private_names", [])
    priv_places = f_cfg.get("private_places", [])
    if priv_names or priv_places:
        n_masked += apply_masklist(blocks, priv_names + priv_places)

    # Mode-specific threshold adjustments
    if mode == "strict":
        for block in blocks:
            score = block.scores.get("privacy_score", 0)
            if score > 3 and (not block.bucket or block.bucket in ("candidates", "micro_style")):
                block.bucket = "rejected"
                block.reasons.append(f"strict_privacy:score={score}")

    elif mode == "recall":
        for block in blocks:
            score = block.scores.get("privacy_score", 0)
            style = block.scores.get("style_score", 0)
            # Lower threshold: >= 5 goes to need_anonymize if has style
            if 5 <= score < 10 and style >= 2 and not block.bucket:
                block.bucket = "need_anonymize"
                block.reasons.append(f"recall_privacy:score={score}")

    # In balanced mode, do nothing (bucket.py handles it)

    return n_masked
