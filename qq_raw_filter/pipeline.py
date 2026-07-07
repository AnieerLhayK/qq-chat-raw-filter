"""
pipeline.py — Pipeline orchestrator for the QQ raw material filter.

Defines the list of named pipeline stages and the runner that executes them
sequentially, collecting per-stage traces into pipeline_trace.json.
"""

from __future__ import annotations

import json
import logging
import random
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from qq_raw_filter.config_loader import snap_config
from qq_raw_filter.qce_parser import load_all_files, is_self
from qq_raw_filter.block_builder import build_blocks_from_messages, MyBlock
from qq_raw_filter.scorer import score_all
from qq_raw_filter.bucket import classify_all
from qq_raw_filter.dedup import dedup_blocks
from qq_raw_filter.filter_applier import apply_drop_sentence_words, apply_masklist
from qq_raw_filter.privacy_filter import filter_privacy_blocks
from qq_raw_filter.style_tagger import tag_style
from qq_raw_filter.review_sampler import write_stratified_samples
from qq_raw_filter.tuning_advice import generate_tuning_advice
from qq_raw_filter.phrase_miner import mine_phrases, generate_phrase_report
from qq_raw_filter.lexicon_auditor import audit_legacy_lexicon

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pipeline types
# ---------------------------------------------------------------------------

class PipelineAbortError(Exception):
    """Raised when a stage fails critically and the pipeline should stop."""


@dataclass
class StageResult:
    """Result from one pipeline stage."""
    name: str
    counts: Dict[str, int] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    trace: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Individual stages
# ---------------------------------------------------------------------------

def stage_parse_files(ctx: Dict[str, Any]) -> StageResult:
    """Scan and parse all QCE files from the input directory."""
    cfg = ctx["config"]
    input_dir = Path(cfg["path"]["input_dir"])
    if not input_dir.exists():
        raise PipelineAbortError(f"Input directory does not exist: {input_dir}")

    limit = ctx.get("args") and getattr(ctx["args"], "limit_files", None)
    messages, file_infos, warnings = load_all_files(input_dir, limit)

    max_msgs = ctx.get("args") and getattr(ctx["args"], "max_messages", None)
    if max_msgs and len(messages) > max_msgs:
        messages = messages[:max_msgs]

    ctx["messages"] = messages
    ctx["file_infos"] = file_infos

    return StageResult(
        name="parse_files",
        counts={"files_found": len(file_infos) + len(warnings),
                "files_parsed": len(file_infos),
                "total_messages": len(messages)},
        warnings=warnings,
    )


def stage_extract_messages(ctx: Dict[str, Any]) -> StageResult:
    """Count messages and identify own messages."""
    messages = ctx.get("messages", [])
    cfg = ctx["config"]
    me_ids = cfg.get("identity", {}).get("me_ids", [])
    me_names = cfg.get("identity", {}).get("me_names", [])

    own_count = sum(1 for m in messages if is_self(m, me_ids, me_names))

    return StageResult(
        name="extract_messages",
        counts={"total": len(messages), "own": own_count},
    )


def stage_normalize_messages(ctx: Dict[str, Any]) -> StageResult:
    """Placeholder for future message normalization (encoding, formatting)."""
    return StageResult(name="normalize_messages", counts={"normalized": len(ctx.get("messages", []))})


def stage_merge_turns(ctx: Dict[str, Any]) -> StageResult:
    """Merge consecutive messages from the same speaker into turns."""
    from qq_raw_filter.block_builder import messages_to_turns

    messages = ctx.get("messages", [])
    cfg = ctx["config"]
    me_ids = cfg.get("identity", {}).get("me_ids", [])
    me_names = cfg.get("identity", {}).get("me_names", [])
    turn_gap = cfg.get("time", {}).get("turn_gap_seconds", 90)

    turns, warnings = messages_to_turns(messages, me_ids, me_names, turn_gap)
    ctx["turns"] = turns

    return StageResult(
        name="merge_turns",
        counts={"turns": len(turns)},
        warnings=warnings,
    )


def stage_split_sessions(ctx: Dict[str, Any]) -> StageResult:
    """Split turns into sessions based on time gaps."""
    from qq_raw_filter.block_builder import turns_to_sessions

    turns = ctx.get("turns", [])
    cfg = ctx["config"]
    session_gap = cfg.get("time", {}).get("session_gap_minutes", 20)
    source_file = ""

    sessions = turns_to_sessions(turns, source_file, session_gap)
    ctx["sessions"] = sessions

    return StageResult(
        name="split_sessions",
        counts={"sessions": len(sessions)},
    )


def stage_extract_my_blocks(ctx: Dict[str, Any]) -> StageResult:
    """Extract my_blocks from sessions using interruption rules."""
    from qq_raw_filter.block_builder import extract_my_blocks

    sessions = ctx.get("sessions", [])
    cfg = ctx["config"]

    blocks, warnings = extract_my_blocks(sessions, cfg)
    ctx["blocks"] = blocks

    return StageResult(
        name="extract_my_blocks",
        counts={"my_blocks": len(blocks)},
        warnings=warnings,
    )


def stage_score_blocks(ctx: Dict[str, Any]) -> StageResult:
    """Score all blocks on style, privacy, junk, chaos."""
    blocks = ctx.get("blocks", [])
    cfg = ctx["config"]

    for block in blocks:
        block.scores = score_all(block, cfg)

    return StageResult(
        name="score_blocks",
        counts={"scored": len(blocks)},
    )


def stage_privacy_filter(ctx: Dict[str, Any]) -> StageResult:
    """Apply privacy mode and mask private names/places."""
    blocks = ctx.get("blocks", [])
    cfg = ctx["config"]

    n_masked = filter_privacy_blocks(blocks, cfg)

    return StageResult(
        name="privacy_filter",
        counts={"masked": n_masked},
    )


def stage_deduplicate(ctx: Dict[str, Any]) -> StageResult:
    """Exact dedup (SHA256 on my_text) — marks duplicates as rejected."""
    blocks = ctx.get("blocks", [])
    cfg = ctx["config"]

    keep_limit = cfg.get("dedup", {}).get("duplicate_keep_limit", 3)
    n_removed, hashes = dedup_blocks(blocks, keep_limit)
    ctx["seen_hashes"] = hashes

    return StageResult(
        name="deduplicate",
        counts={"exact_duplicates_removed": n_removed},
    )


def stage_apply_filters(ctx: Dict[str, Any]) -> StageResult:
    """Apply drop_sentence_words and mask_words from config (lexicon-first)."""
    blocks = ctx.get("blocks", [])
    cfg = ctx["config"]
    lex = cfg.get("_lexicon", {})

    # Lexicon-first with TOML fallback
    if lex and lex.get("drop_sentence_phrases"):
        drop_words = lex["drop_sentence_phrases"]
    else:
        drop_words = cfg.get("filter", {}).get("drop_sentence_words", [])

    if lex and lex.get("mask_phrases"):
        mask_words = lex["mask_phrases"]
    else:
        mask_words = cfg.get("filter", {}).get("mask_words", [])

    f_cfg = cfg.get("filter", {})
    priv_names = f_cfg.get("private_names", [])
    priv_places = f_cfg.get("private_places", [])

    dropped = apply_drop_sentence_words(blocks, drop_words)
    n_masked = apply_masklist(blocks, mask_words + priv_names + priv_places)

    return StageResult(
        name="apply_filters",
        counts={"dropped_by_blocklist": dropped, "words_masked": n_masked},
    )


def stage_bucket_decision(ctx: Dict[str, Any]) -> StageResult:
    """Classify all blocks into buckets."""
    blocks = ctx.get("blocks", [])
    cfg = ctx["config"]

    bucketed = classify_all(blocks, cfg)
    ctx["buckets"] = bucketed

    return StageResult(
        name="bucket_decision",
        counts={k: len(v) for k, v in bucketed.items()},
    )


def stage_tag_style(ctx: Dict[str, Any]) -> StageResult:
    """Add style_tags to each block."""
    blocks = ctx.get("blocks", [])
    cfg = ctx["config"]

    n_tagged = tag_style(blocks, cfg)

    return StageResult(
        name="tag_style",
        counts={"blocks_tagged": n_tagged},
    )


def stage_audit_legacy_lexicon(ctx: Dict[str, Any]) -> StageResult:
    """Run frequency audit of archived lexicon terms against classified blocks.

    Only runs if [lexicon.audit].enabled is true.  Requires archive terms
    to have been loaded by inject_lexicon() at startup.
    """
    audit_cfg = ctx["config"].get("lexicon", {}).get("audit", {})
    if not audit_cfg.get("enabled", True):
        return StageResult(name="audit_legacy_lexicon", counts={"enabled": 0})

    archive_terms = ctx["config"].get("_archive_terms", [])
    if not archive_terms:
        return StageResult(
            name="audit_legacy_lexicon",
            counts={"archived_terms_available": 0},
            warnings=["No archive terms loaded — run archive_lexicons.py first"],
        )

    blocks = ctx.get("blocks", [])
    bucketed = ctx.get("buckets", {})
    cfg = ctx["config"]
    run_dir = ctx.get("run_dir")

    # Write audit output to phrase_mining/ subdirectory when available
    audit_run_dir = run_dir
    if run_dir:
        pm_dir = run_dir / "phrase_mining"
        pm_dir.mkdir(exist_ok=True)
        audit_run_dir = pm_dir

    result = audit_legacy_lexicon(blocks, bucketed, archive_terms, cfg, audit_run_dir)
    ctx["lexicon_audit"] = result

    stats = result.get("stats", {})
    return StageResult(
        name="audit_legacy_lexicon",
        counts={
            "terms_audited": stats.get("total_terms_audited", 0),
            "total_occurrences": stats.get("total_occurrences", 0),
            "promote_to_active": stats.get("promote_to_active", 0),
            "keep_as_candidate": stats.get("keep_as_candidate", 0),
            "demote_or_remove": stats.get("demote_or_remove", 0),
            "zero_frequency": stats.get("zero_frequency_terms", 0),
        },
    )


def stage_write_outputs(ctx: Dict[str, Any]) -> StageResult:
    """Write all bucket JSONL files, config snapshot, stats, debug sessions."""
    cfg = ctx["config"]
    args = ctx.get("args")
    bucketed = ctx.get("buckets", {})
    blocks = ctx.get("blocks", [])
    run_dir = ctx.get("run_dir")

    if not run_dir:
        # Dry-run: skip writing but still return counts
        return StageResult(
            name="write_outputs",
            counts={k: len(v) for k, v in bucketed.items()},
        )

    warnings: List[str] = []

    # Write bucket files
    for bn in ("candidates", "micro_style", "need_anonymize", "chaos_style", "debatable", "rejected"):
        bucket_blocks = bucketed.get(bn, [])
        if bn == "rejected" and not cfg.get("output", {}).get("keep_rejected", True):
            continue
        _write_jsonl(bucket_blocks, run_dir / f"{bn}.jsonl")

    # Config snapshot
    if cfg.get("output", {}).get("write_active_config_snapshot", True):
        (run_dir / "active_config.toml").write_text(snap_config(cfg), encoding="utf-8")

    # Debug sessions
    if cfg.get("output", {}).get("write_debug_sessions", True):
        debug_entries = []
        for b in blocks:
            debug_entries.append({
                "block_id": b.block_id,
                "session_id": b.session_id,
                "bucket": b.bucket,
                "source_file": b.source_file,
                "my_text_preview": b.my_text[:200],
                "metrics": b.metrics,
                "scores": b.scores,
                "reasons": b.reasons,
                "style_tags": getattr(b, "style_tags", []),
            })
        _write_jsonl(debug_entries, run_dir / "debug_sessions.jsonl")

    # Stats
    stats = _build_stats(bucketed, blocks, ctx)
    ctx["stats"] = stats
    with (run_dir / "stats.json").open("w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

    o = stats.get("outputs", {})
    logger.info("Results: candidates=%d micro_style=%d need_anonymize=%d chaos_style=%d debatable=%d rejected=%d",
                o.get("candidates", 0), o.get("micro_style", 0),
                o.get("need_anonymize", 0), o.get("chaos_style", 0),
                o.get("debatable", 0), o.get("rejected", 0))

    return StageResult(
        name="write_outputs",
        counts={k: len(v) for k, v in bucketed.items()},
    )


def stage_write_review_samples(ctx: Dict[str, Any]) -> StageResult:
    """Write stratified review samples."""
    cfg = ctx["config"]
    bucketed = ctx.get("buckets", {})
    run_dir = ctx.get("run_dir")

    if not run_dir:
        return StageResult(name="write_review_samples", counts={})

    if not cfg.get("output", {}).get("write_human_review_samples", True):
        return StageResult(name="write_review_samples", counts={})

    sample_size = cfg.get("output", {}).get("human_review_sample_size", 100)
    review_dir = run_dir / "review_samples"
    review_dir.mkdir(exist_ok=True)

    n_written = write_stratified_samples(bucketed, review_dir, sample_size)

    return StageResult(
        name="write_review_samples",
        counts={"sample_files_written": n_written},
    )


def stage_mine_phrases(ctx: Dict[str, Any]) -> StageResult:
    """Run phrase mining on all blocks to discover 2-4 char n-gram candidates.

    Uses the bucket classification from the bucket_decision stage to compute
    bucket-aware metrics (style_keyness, chaos_rate, privacy_bucket_rate).
    Writes review files and a human-readable report to the run directory.
    """
    blocks = ctx.get("blocks", [])
    bucketed = ctx.get("buckets", {})
    cfg = ctx["config"]
    lexicon = cfg.get("_lexicon", {})
    run_dir = ctx.get("run_dir")

    pm_cfg = cfg.get("phrase_mining", {})
    if not pm_cfg.get("enabled", False):
        return StageResult(name="mine_phrases", counts={"enabled": 0})

    mining_result = mine_phrases(blocks, bucketed, cfg, lexicon)
    ctx["phrase_mining"] = mining_result
    stats = mining_result.get("stats", {})

    # Write output files if we have a run_dir
    if run_dir:
        pm_dir = run_dir / "phrase_mining"
        pm_dir.mkdir(exist_ok=True)

        # Write per-length frequency files
        out_cfg = pm_cfg.get("output", {})
        if out_cfg.get("write_phrase_freq_by_length", True):
            for n, key in [(2, "phrase_freq_2gram"), (3, "phrase_freq_3gram"), (4, "phrase_freq_4gram")]:
                entries = mining_result.get(key, [])
                if entries:
                    _write_jsonl(entries, pm_dir / f"phrase_freq_{n}gram.jsonl")

        # Write phrase candidates
        if out_cfg.get("write_phrase_candidates", True):
            candidates = mining_result.get("phrase_candidates", [])
            if candidates:
                _write_jsonl(candidates, pm_dir / "phrase_candidates.jsonl")

        # Write review files
        for name in ("review_phrases_top", "review_privacy_terms", "review_chaos_terms", "review_stopword_candidates"):
            entries = mining_result.get(name, [])
            if entries:
                _write_jsonl(entries, pm_dir / f"{name}.jsonl")

        # Write stats
        with (pm_dir / "phrase_mining_stats.json").open("w", encoding="utf-8") as f:
            json.dump(stats, f, ensure_ascii=False, indent=2)

        # Write report
        if out_cfg.get("write_phrase_report", True):
            generate_phrase_report(mining_result, cfg, pm_dir)

        # Update auto_phrase_candidates in lexicon directory
        if out_cfg.get("update_auto_phrase_candidates", True):
            candidates = mining_result.get("phrase_candidates", [])
            if candidates:
                lex_cfg = cfg.get("lexicon", {})
                auto_path = lex_cfg.get("phrase_candidates_path", "")
                if auto_path:
                    ap = Path(auto_path)
                    if ap.parent.exists():
                        # Merge: don't overwrite, append new ones
                        existing_phrases = set()
                        if ap.exists():
                            with ap.open("r", encoding="utf-8") as f:
                                for line in f:
                                    line = line.strip()
                                    if line:
                                        try:
                                            entry = json.loads(line)
                                            existing_phrases.add(entry.get("phrase", ""))
                                        except json.JSONDecodeError:
                                            pass
                        new_count = 0
                        with ap.open("a", encoding="utf-8") as f:
                            for c in candidates:
                                if c.get("phrase") not in existing_phrases:
                                    c["status"] = "auto_candidate"
                                    f.write(json.dumps(c, ensure_ascii=False) + "\n")
                                    new_count += 1
                        logger.info("Phrase mining: appended %d new candidates to %s", new_count, ap)
                        stats["new_candidates_appended"] = new_count

    return StageResult(
        name="mine_phrases",
        counts={
            "total_candidates": stats.get("total_candidates", 0),
            "by_length": str(stats.get("by_length", {})),
            "privacy_candidates": stats.get("privacy_candidates", 0),
            "chaos_candidates": stats.get("chaos_candidates", 0),
            "stopword_candidates": stats.get("stopword_candidates", 0),
        },
    )


# ---------------------------------------------------------------------------
# Pipeline definition
# ---------------------------------------------------------------------------

PIPELINE = [
    ("parse_files", stage_parse_files),
    ("extract_messages", stage_extract_messages),
    ("normalize_messages", stage_normalize_messages),
    ("merge_turns", stage_merge_turns),
    ("split_sessions", stage_split_sessions),
    ("extract_my_blocks", stage_extract_my_blocks),
    ("score_blocks", stage_score_blocks),
    ("privacy_filter", stage_privacy_filter),
    ("deduplicate", stage_deduplicate),
    ("apply_filters", stage_apply_filters),
    ("bucket_decision", stage_bucket_decision),
    ("audit_legacy_lexicon", stage_audit_legacy_lexicon),
    ("tag_style", stage_tag_style),
    ("mine_phrases", stage_mine_phrases),
    ("write_outputs", stage_write_outputs),
    ("write_review_samples", stage_write_review_samples),
]


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------

def run_pipeline(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Execute all pipeline stages sequentially."""
    started_at = datetime.now(timezone.utc)
    start_ts = time.monotonic()

    ctx.setdefault("stage_results", {})

    for name, stage_fn in PIPELINE:
        logger.info("Pipeline stage: %s", name)
        try:
            result = stage_fn(ctx)
        except PipelineAbortError as e:
            logger.error("Pipeline aborted at stage '%s': %s", name, e)
            ctx["stage_results"][name] = StageResult(
                name=name, counts={}, warnings=[str(e)])
            break
        except Exception as e:
            logger.exception("Pipeline stage '%s' failed: %s", name, e)
            ctx["stage_results"][name] = StageResult(
                name=name, counts={}, warnings=[f"EXCEPTION: {e}"])
            break

        ctx["stage_results"][name] = result
        logger.info("  -> %s", result.counts)

    elapsed = time.monotonic() - start_ts

    # Write pipeline_trace.json
    _write_trace(ctx, started_at, elapsed)

    return ctx.get("stats", {})


# ---------------------------------------------------------------------------
# Trace writer
# ---------------------------------------------------------------------------

def _write_trace(ctx: Dict[str, Any], started_at: datetime, elapsed: float) -> None:
    """Write pipeline_trace.json to the run directory."""
    run_dir = ctx.get("run_dir")
    if not run_dir:
        return

    stages_out = []
    for name, _ in PIPELINE:
        sr = ctx["stage_results"].get(name)
        if sr:
            stages_out.append({
                "name": sr.name,
                "counts": sr.counts,
                "warnings": sr.warnings[:20] if sr.warnings else [],
                "warnings_truncated": len(sr.warnings) > 20,
            })

    outputs = ctx.get("stats", {}).get("outputs", {})

    trace = {
        "pipeline": "qq_raw_material_filter",
        "config_version": ctx["config"].get("meta", {}).get("config_version", "0.1"),
        "started_at": started_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "elapsed_seconds": round(elapsed, 2),
        "total_blocks": len(ctx.get("blocks", [])),
        "outputs": outputs,
        "stages": stages_out,
    }

    (run_dir / "pipeline_trace.json").write_text(
        json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")

    # Also write tuning advice
    if ctx["config"].get("tuning_advice", {}).get("enable", True):
        try:
            generate_tuning_advice(ctx)
        except Exception as e:
            logger.warning("Tuning advice generation failed: %s", e)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_jsonl(items: List[Any], path: Path) -> None:
    """Write a list of objects to a JSONL file."""
    with path.open("w", encoding="utf-8") as f:
        for item in items:
            if hasattr(item, 'to_dict'):
                data = item.to_dict()
            elif isinstance(item, dict):
                data = item
            else:
                data = {"text": str(item)}
            # Ensure style_tags is included
            if hasattr(item, 'style_tags') and item.style_tags:
                data["style_tags"] = item.style_tags
            f.write(json.dumps(data, ensure_ascii=False) + "\n")


def _build_stats(
    bucketed: Dict[str, List[Any]],
    blocks: List[MyBlock],
    ctx: Dict[str, Any],
) -> Dict[str, Any]:
    """Build stats.json from pipeline state."""
    file_infos = ctx.get("file_infos", [])
    messages = ctx.get("messages", [])
    cfg = ctx["config"]

    me_ids = cfg.get("identity", {}).get("me_ids", [])
    me_names = cfg.get("identity", {}).get("me_names", [])
    own_msg_count = sum(1 for m in messages if is_self(m, me_ids, me_names))

    top_reasons: Dict[str, int] = {}
    for bucket_blocks in bucketed.values():
        for b in bucket_blocks:
            for r in b.reasons:
                top_reasons[r] = top_reasons.get(r, 0) + 1
    sorted_reasons = sorted(top_reasons.items(), key=lambda x: -x[1])[:20]

    all_warnings: List[str] = []
    for sr in ctx.get("stage_results", {}).values():
        all_warnings.extend(sr.warnings)

    # Use parse_files stage count for accurate file count
    parse_sr = ctx.get("stage_results", {}).get("parse_files")
    files_found = parse_sr.counts.get("files_found", len(file_infos)) if parse_sr else len(file_infos)

    return {
        "total_files_found": files_found,
        "total_files_parsed": len(file_infos),
        "total_messages": len(messages),
        "own_messages": own_msg_count,
        "sessions": len(ctx.get("sessions", [])),
        "turns": len(ctx.get("turns", [])),
        "my_blocks": len(blocks),
        "outputs": {k: len(v) for k, v in bucketed.items()},
        "top_reject_reasons": [
            {"reason": r, "count": c} for r, c in sorted_reasons
        ],
        "warnings": all_warnings[:50],
        "warnings_truncated": len(all_warnings) > 50,
    }
