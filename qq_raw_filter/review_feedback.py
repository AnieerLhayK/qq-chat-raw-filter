"""Local review-decision support for phrase and block feedback.

Decision files stay next to the private lexicons, never in this source tree.
Each JSONL record is one of:
  {"kind": "phrase", "key": "phrase", "decision": "keep|drop", "reason": "..."}
  {"kind": "block", "key": "<review_id>", "decision": "keep|drop", "reason": "..."}
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, Iterable

from qq_raw_filter.block_builder import MyBlock

logger = logging.getLogger(__name__)


def block_review_id(block: MyBlock) -> str:
    """Return a stable local identifier without placing source text in labels."""
    source = block.source_file or "unknown-source"
    material = f"{source}\0{block.session_id}\0{block.my_text}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def attach_review_ids(blocks: Iterable[MyBlock]) -> None:
    for block in blocks:
        block.metrics["review_id"] = block_review_id(block)


def load_review_decisions(path: Path) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Load valid local decisions; later records intentionally supersede earlier ones."""
    decisions: Dict[str, Dict[str, Dict[str, Any]]] = {"phrase": {}, "block": {}}
    if not path.exists():
        logger.info("Review decision file not found, continuing without feedback: %s", path)
        return decisions

    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Skipping invalid review decision at %s:%d", path, line_number)
                continue
            kind = entry.get("kind")
            key = str(entry.get("key", "")).strip()
            decision = entry.get("decision")
            if kind not in decisions or not key or decision not in {"keep", "drop"}:
                logger.warning("Skipping invalid review decision at %s:%d", path, line_number)
                continue
            decisions[kind][key] = entry
    return decisions


def apply_block_review_decisions(
    blocks: Iterable[MyBlock],
    decisions: Dict[str, Dict[str, Dict[str, Any]]],
) -> Dict[str, int]:
    """Apply drops now and defer accepted blocks to safe bucket classification."""
    counts = {"manual_keep_requested": 0, "manual_drop_applied": 0, "blocked_by_filter": 0}
    block_decisions = decisions.get("block", {})
    for block in blocks:
        review_id = block.metrics.get("review_id")
        entry = block_decisions.get(review_id)
        if not entry:
            continue
        decision = entry["decision"]
        if decision == "drop":
            block.bucket = "rejected"
            block.reasons.append("manual_review_drop")
            counts["manual_drop_applied"] += 1
        elif block.bucket:
            # Pre-existing rejected states are privacy/filter/dedup safety rails.
            block.reasons.append("manual_review_keep_blocked_by_prior_filter")
            counts["blocked_by_filter"] += 1
        else:
            block.metrics["manual_review_decision"] = "keep"
            block.reasons.append("manual_review_keep_requested")
            counts["manual_keep_requested"] += 1
    return counts
