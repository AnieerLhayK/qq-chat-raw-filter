#!/usr/bin/env python3
"""
qce_block_filter.py — QQ Chat Exporter raw material filter / normalizer.

Processes raw QCE v5 JSON exports into structured, bucketd output for
subsequent character skill extraction.

Usage:
  python qce_block_filter.py --me-id 123456789
  python qce_block_filter.py --config path/to/filter_config.toml --me-name "MyName"
  python qce_block_filter.py --dry-run --limit-files 2
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from config_loader import load_config, resolve_paths, snap_config
from qce_parser import load_all_files, is_self
from block_builder import build_blocks_from_messages
from bucket import classify_all

logger = logging.getLogger(__name__)


AI_ROOT = Path("D:/AI").resolve()


def setup_logging(debug: bool = False) -> None:
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="QQ Chat Exporter raw material filter / normalizer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s --me-id 123456789\n"
            "  %(prog)s --config my_config.toml --me-name \"MyName\"\n"
            "  %(prog)s --dry-run --limit-files 2\n"
            "  %(prog)s --include-all-speakers --debug\n"
        ),
    )
    parser.add_argument("--config", default=None, help="Path to filter_config.toml")
    parser.add_argument("--input-dir", default=None, help="Override input directory")
    parser.add_argument("--output-dir", default=None, help="Override output directory")
    parser.add_argument("--me-id", nargs="*", default=[], help="My QQ number(s) or UID(s)")
    parser.add_argument("--me-name", nargs="*", default=[], help="My nickname(s)")
    parser.add_argument("--include-all-speakers", action="store_true",
                        help="Debug: include all speakers without filtering")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, no output files")
    parser.add_argument("--limit-files", type=int, default=None, help="Limit input files")
    parser.add_argument("--max-messages", type=int, default=None, help="Limit total messages")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    return parser.parse_args(argv)


def _resolve_config_path(custom_path: Optional[str]) -> Path:
    if custom_path:
        return Path(custom_path)
    return Path(__file__).resolve().parent / "filter_config.toml"


def _make_run_dir(output_base: Path) -> Path:
    now = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = output_base / f"run_{now}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _write_jsonl(items: List[Any], path: Path) -> None:
    with path.open("w", encoding="utf-8") as f:
        for item in items:
            if hasattr(item, 'to_dict'):
                data = item.to_dict()
            elif isinstance(item, dict):
                data = item
            else:
                data = {"text": str(item)}
            f.write(json.dumps(data, ensure_ascii=False) + "\n")


def _sample_review(blocks: List[Any], sample_size: int) -> List[Any]:
    if len(blocks) <= sample_size:
        return list(blocks)
    return random.sample(blocks, sample_size)


def _build_stats(
    blocks: Dict[str, List[Any]],
    files_found: int, files_parsed: int,
    total_msgs: int, own_msgs: int,
    sessions: int, turns: int, my_blocks: int,
    warnings: List[str],
) -> Dict[str, Any]:
    top_reasons: Dict[str, int] = {}
    for bucket_blocks in blocks.values():
        for b in bucket_blocks:
            for r in b.reasons:
                top_reasons[r] = top_reasons.get(r, 0) + 1
    sorted_reasons = sorted(top_reasons.items(), key=lambda x: -x[1])[:20]

    return {
        "total_files_found": files_found,
        "total_files_parsed": files_parsed,
        "total_messages": total_msgs,
        "own_messages": own_msgs,
        "sessions": sessions,
        "turns": turns,
        "my_blocks": my_blocks,
        "outputs": {k: len(v) for k, v in blocks.items()},
        "top_reject_reasons": [
            {"reason": r, "count": c} for r, c in sorted_reasons
        ],
        "warnings": warnings[:50],
        "warnings_truncated": len(warnings) > 50,
    }


def run_filter(args: argparse.Namespace, config: Dict[str, Any]) -> Dict[str, Any]:
    """Execute the full filter pipeline."""
    cfg = resolve_paths(config, AI_ROOT)
    input_dir = Path(args.input_dir or cfg["path"]["input_dir"])
    output_base = Path(args.output_dir or cfg["path"]["output_dir"])

    if not input_dir.exists():
        logger.error("Input directory does not exist: %s", input_dir)
        sys.exit(1)

    me_ids: List[str] = list(args.me_id) if args.me_id else list(cfg.get("identity", {}).get("me_ids", []))
    me_names: List[str] = list(args.me_name) if args.me_name else list(cfg.get("identity", {}).get("me_names", []))

    if not args.include_all_speakers and not me_ids and not me_names:
        logger.error("No identity configured. Use --me-id, --me-name, --include-all-speakers, or [identity] in TOML.")
        sys.exit(1)

    logger.info("Scanning input: %s", input_dir)
    all_messages, file_infos, parse_warnings = load_all_files(input_dir, args.limit_files)
    logger.info("Parsed %d messages from %d files", len(all_messages), len(file_infos))

    if not all_messages:
        logger.warning("No messages found.")
        return _build_stats({}, len(file_infos), 0, 0, 0, 0, 0, 0, parse_warnings)

    if args.max_messages and len(all_messages) > args.max_messages:
        all_messages = all_messages[:args.max_messages]

    own_msg_count = sum(1 for m in all_messages if is_self(m, me_ids, me_names))
    logger.info("Own messages: %d / %d", own_msg_count, len(all_messages))

    if args.dry_run:
        logger.info("DRY RUN: stopping after parsing.")
        logger.info("  Messages: %d, Own: %d, Files: %d",
                     len(all_messages), own_msg_count, len(file_infos))
        return {}

    logger.info("Building turns, sessions, and my_blocks...")
    source_file = file_infos[0]["file"] if file_infos else str(input_dir)
    blocks, session_sources, block_warnings = build_blocks_from_messages(
        all_messages, source_file, cfg, me_ids, me_names,
    )
    all_warnings = parse_warnings + block_warnings
    logger.info("Built %d my_blocks", len(blocks))

    logger.info("Scoring and classifying...")
    bucketed = classify_all(blocks, cfg)
    for bn, bv in bucketed.items():
        logger.info("  %s: %d", bn, len(bv))

    # Enrich context
    for bucket_blocks in bucketed.values():
        for b in bucket_blocks:
            enriched = []
            for turn in b.context:
                if hasattr(turn, 'to_dict'):
                    d = turn.to_dict()
                    d["speaker_type"] = turn.speaker_type
                    enriched.append(d)
                else:
                    enriched.append(turn)
            b.context = enriched  # type: ignore

    if not args.dry_run:
        run_dir = _make_run_dir(output_base)
        logger.info("Output directory: %s", run_dir)

        for bn in ("candidates", "micro_style", "need_anonymize", "chaos_style", "rejected"):
            bucket_blocks = bucketed[bn]
            if bn == "rejected" and not cfg.get("output", {}).get("keep_rejected", True):
                continue
            _write_jsonl(bucket_blocks, run_dir / f"{bn}.jsonl")

        if cfg.get("output", {}).get("write_active_config_snapshot", True):
            (run_dir / "active_config.toml").write_text(snap_config(cfg), encoding="utf-8")

        stats = _build_stats(
            bucketed,
            files_found=len(file_infos) + len(parse_warnings),
            files_parsed=len(file_infos),
            total_msgs=len(all_messages),
            own_msgs=own_msg_count,
            sessions=len(set(session_sources)),
            turns=0,  # turn count computed from block metrics
            my_blocks=len(blocks),
            warnings=all_warnings,
        )
        with (run_dir / "stats.json").open("w", encoding="utf-8") as f:
            json.dump(stats, f, ensure_ascii=False, indent=2)

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
                })
            _write_jsonl(debug_entries, run_dir / "debug_sessions.jsonl")

        # Review samples
        if cfg.get("output", {}).get("write_human_review_samples", True):
            sample_size = cfg.get("output", {}).get("human_review_sample_size", 100)
            review_dir = run_dir / "review_samples"
            review_dir.mkdir(exist_ok=True)
            sample_map = [
                ("candidates", "candidates_sample.jsonl"),
                ("rejected", "rejected_sample.jsonl"),
                ("chaos_style", "chaos_style_sample.jsonl"),
                ("micro_style", "micro_style_sample.jsonl"),
                ("need_anonymize", "need_anonymize_sample.jsonl"),
            ]
            for bucket_name, filename in sample_map:
                if bucketed[bucket_name]:
                    _write_jsonl(
                        _sample_review(bucketed[bucket_name], sample_size),
                        review_dir / filename,
                    )
            logger.info("Review samples written to %s", review_dir)

        logger.info("Filter completed. Output in: %s", run_dir)
        return stats

    return {}


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    setup_logging(args.debug)

    config_path = _resolve_config_path(args.config)
    logger.info("Config: %s", config_path if config_path.exists() else "(defaults)")

    try:
        config = load_config(config_path if config_path.exists() else None)
    except Exception as e:
        logger.error("Config error: %s", e)
        return 1

    if args.me_id:
        config.setdefault("identity", {})["me_ids"] = list(args.me_id)
    if args.me_name:
        config.setdefault("identity", {})["me_names"] = list(args.me_name)
    if args.input_dir:
        config.setdefault("path", {})["input_dir"] = args.input_dir
    if args.output_dir:
        config.setdefault("path", {})["output_dir"] = args.output_dir

    logger.info("Identity: ids=%s names=%s",
                 config.get("identity", {}).get("me_ids", []),
                 config.get("identity", {}).get("me_names", []))

    stats = run_filter(args, config)
    if stats:
        o = stats.get("outputs", {})
        logger.info("Results: candidates=%d micro_style=%d need_anonymize=%d chaos_style=%d rejected=%d",
                     o.get("candidates", 0), o.get("micro_style", 0),
                     o.get("need_anonymize", 0), o.get("chaos_style", 0),
                     o.get("rejected", 0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
