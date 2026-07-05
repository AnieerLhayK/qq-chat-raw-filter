"""
tuning_advice.py — Auto-generate tuning_advice.md from pipeline output ratios.

Reads the pipeline context after a run and produces a markdown advisory file
with observations and tuning suggestions. Does NOT modify any configuration.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from block_builder import MyBlock

logger = logging.getLogger(__name__)


def generate_tuning_advice(ctx: Dict[str, Any]) -> None:
    """Generate tuning_advice.md in the run directory."""
    run_dir = ctx.get("run_dir")
    if not run_dir:
        return

    stats = ctx.get("stats", {})
    config = ctx.get("config", {})
    ta_cfg = config.get("tuning_advice", {})
    outputs = stats.get("outputs", {})
    top_reasons = stats.get("top_reject_reasons", [])

    total = stats.get("my_blocks", 0)
    candidates = outputs.get("candidates", 0)
    micro = outputs.get("micro_style", 0)
    need_anon = outputs.get("need_anonymize", 0)
    chaos = outputs.get("chaos_style", 0)
    debatable = outputs.get("debatable", 0)
    rejected = outputs.get("rejected", 0)

    candidate_ratio = candidates / total if total > 0 else 0
    chaos_ratio = chaos / total if total > 0 else 0

    target_min = ta_cfg.get("candidate_ratio_target_min", 0.05)
    target_max = ta_cfg.get("candidate_ratio_target_max", 0.12)
    chaos_max = ta_cfg.get("chaos_style_ratio_max", 0.10)

    lines: List[str] = []
    lines.append(f"# Tuning Advice — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC")
    lines.append("")
    lines.append("## Overview")
    lines.append("")
    lines.append(f"- Total blocks: {total}")
    lines.append(f"- Candidates: {candidates} ({candidate_ratio:.1%})")
    lines.append(f"- Micro style: {micro} ({micro / total:.1%})" if total > 0 else f"- Micro style: {micro}")
    lines.append(f"- Chaos style: {chaos} ({chaos_ratio:.1%})")
    lines.append(f"- Debatable: {debatable} ({debatable / total:.1%})" if total > 0 else f"- Debatable: {debatable}")
    lines.append(f"- Need anonymize: {need_anon}")
    lines.append(f"- Rejected: {rejected} ({rejected / total:.1%})" if total > 0 else f"- Rejected: {rejected}")
    lines.append("")

    observations: List[str] = []

    # 1. Candidate ratio
    if candidate_ratio < target_min:
        observations.append(
            f"1. **Candidates below target**: {candidate_ratio:.1%} vs target {target_min:.0%}-{target_max:.0%}. "
            f"Consider lowering `candidate_min_style_score` (currently "
            f"{config.get('score', {}).get('candidate_min_style_score', 5)}), "
            f"or lowering `candidate_min_chars_when_style_high`."
        )
    elif candidate_ratio > target_max:
        observations.append(
            f"1. **Candidates above target**: {candidate_ratio:.1%} vs target {target_min:.0%}-{target_max:.0%}. "
            f"Consider raising `candidate_min_style_score` "
            f"(currently {config.get('score', {}).get('candidate_min_style_score', 5)}), "
            f"or tightening quality gates."
        )

    # 2. Chaos style ratio
    if chaos_ratio > chaos_max:
        observations.append(
            f"2. **Chaos style high**: {chaos_ratio:.1%} vs max {chaos_max:.0%}. "
            f"Consider raising `chaos_separate_score` "
            f"(currently {config.get('score', {}).get('chaos_separate_score', 2)}), "
            f"or trimming chaos word lists."
        )

    # 3. Top reject reasons analysis
    reason_counts = {r["reason"]: r["count"] for r in top_reasons[:5]}

    too_few_chars_total = sum(
        c for k, c in reason_counts.items() if k.startswith("too_few_chars:"))
    if too_few_chars_total > total * 0.1 and total > 0:
        min_chars = config.get("my_block", {}).get("min_my_block_chars", 20)
        observations.append(
            f"3. **too_few_chars dominates rejected**: {too_few_chars_total}/{rejected} rejected "
            f"for short text (min_my_block_chars={min_chars}). "
            f"Consider lowering to {max(min_chars - 5, 5)}."
        )

    if "low_style_but_has_tone" in reason_counts:
        count = reason_counts["low_style_but_has_tone"]
        observations.append(
            f"4. **low_style_but_has_tone**: {count} blocks — short tone-only reactions. "
            f"Current micro_style_min_style_score={config.get('score', {}).get('micro_style_min_style_score', 2)}. "
            f"Consider raising to 2.5-3.0 if too noisy, or keeping 2.0 if valuable as personality markers."
        )

    # BUGFIX: previously used next(iter(...)) which only checked the first key
    has_fallback = any(k.startswith("rejected_fallback") for k in reason_counts)
    if has_fallback:
        fb_count = sum(c for k, c in reason_counts.items() if k.startswith("rejected_fallback"))
        observations.append(
            f"5. **rejected_fallback**: {fb_count} blocks hit the fallback path. "
            f"Check `top_reject_reasons` in stats.json for the dominant fallback signal."
        )

    has_duplicate = any("duplicate" in k for k in reason_counts)
    if has_duplicate:
        dup_count = sum(c for k, c in reason_counts.items() if "duplicate" in k)
        if dup_count > max(total * 0.01, 5) and total > 0:
            observations.append(
                f"6. **Exact duplicates**: {dup_count} blocks removed. "
                f"Current duplicate_keep_limit={config.get('dedup', {}).get('duplicate_keep_limit', 3)}. "
                f"Consider lowering the limit."
            )

    if not observations:
        observations.append("No specific tuning advice — output ratios are within expected ranges.")

    lines.append("## Observations")
    lines.append("")
    lines.extend(observations)
    lines.append("")

    # Config snapshot for reference
    lines.append("## Current Thresholds")
    lines.append("")
    sc = config.get("score", {})
    lines.append(f"- candidate_min_style_score = {sc.get('candidate_min_style_score', 5)}")
    lines.append(f"- candidate_min_chars_when_style_high = {sc.get('candidate_min_chars_when_style_high', 30)}")
    lines.append(f"- micro_style_min_style_score = {sc.get('micro_style_min_style_score', 2)}")
    lines.append(f"- chaos_separate_score = {sc.get('chaos_separate_score', 2)}")
    lines.append(f"- junk_reject_score = {sc.get('junk_reject_score', 5)}")
    lines.append(f"- debatable_max_per_run = {sc.get('debatable_max_per_run', 200)}")
    lines.append(f"- min_my_block_chars = {config.get('my_block', {}).get('min_my_block_chars', 20)}")
    lines.append("")
    lines.append("---")
    lines.append("*This advice is auto-generated and advisory only. It does not change any configuration.*")

    content = "\n".join(lines)
    (run_dir / "tuning_advice.md").write_text(content, encoding="utf-8")
    logger.info("Tuning advice written to tuning_advice.md")
