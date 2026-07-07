#!/usr/bin/env python3
"""
qce_block_filter.py — QQ Chat Exporter raw material filter / normalizer.

Processes raw QCE v5 JSON exports into structured, bucketed output for
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
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from qq_raw_filter.config_loader import load_config, resolve_paths
from qq_raw_filter.lexicon_loader import inject_lexicon
from qq_raw_filter.pipeline import run_pipeline, PipelineAbortError

logger = logging.getLogger(__name__)

def _get_ai_root() -> Path:
    """Resolve AI_ROOT: env var AI_ROOT > platform default (D:/AI)."""
    env_root = os.environ.get("AI_ROOT")
    if env_root:
        return Path(env_root).resolve()
    return Path("D:/AI").resolve()

AI_ROOT = _get_ai_root()


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
    parser.add_argument("--mine-phrases", action="store_const", const=True, default=None,
                        help="Enable phrase mining (overrides config)")
    parser.add_argument("--skip-phrase-mining", action="store_const", const=True, default=None,
                        help="Disable phrase mining (overrides config)")
    parser.add_argument("--update-lexicon", action="store_const", const=True, default=None,
                        help="Update phrase_candidates.jsonl with discovered phrases")
    parser.add_argument("--force-update-phrase-bank", action="store_const", const=True, default=None,
                        help="Allow updating phrase_bank.jsonl (dangerous)")
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

    # Apply CLI overrides
    if args.me_id:
        config.setdefault("identity", {})["me_ids"] = list(args.me_id)
    if args.me_name:
        config.setdefault("identity", {})["me_names"] = list(args.me_name)
    if args.input_dir:
        config.setdefault("path", {})["input_dir"] = args.input_dir
    if args.output_dir:
        config.setdefault("path", {})["output_dir"] = args.output_dir

    config = resolve_paths(config, AI_ROOT)

    # Apply overrides for phrase_mining
    if args.mine_phrases is not None:
        config.setdefault("phrase_mining", {})["enabled"] = args.mine_phrases
    if args.skip_phrase_mining is not None:
        config.setdefault("phrase_mining", {})["enabled"] = not args.skip_phrase_mining
    if args.update_lexicon is not None:
        config.setdefault("phrase_mining", {}).setdefault("output", {})["update_auto_phrase_candidates"] = args.update_lexicon
    if args.force_update_phrase_bank is not None:
        config.setdefault("phrase_mining", {}).setdefault("output", {})["update_phrase_bank"] = args.force_update_phrase_bank

    # Inject lexicon into config for downstream consumption
    config = inject_lexicon(config, AI_ROOT)

    logger.info("Identity: ids=%s names=%s",
                 config.get("identity", {}).get("me_ids", []),
                 config.get("identity", {}).get("me_names", []))
    logger.info("Phrase mining: %s, Lexicon loaded: %d entries",
                 config.get("phrase_mining", {}).get("enabled", False),
                 len(config.get("_lexicon", {}).get("phrase_bank", [])))

    if not args.include_all_speakers and not config.get("identity", {}).get("me_ids") and not config.get("identity", {}).get("me_names"):
        logger.error("No identity configured. Use --me-id, --me-name, --include-all-speakers, or [identity] in TOML.")
        return 1

    # Build pipeline context
    output_base = Path(config["path"]["output_dir"])
    run_dir = _make_run_dir(output_base) if not args.dry_run else None
    logger.info("Output directory: %s", run_dir or "(dry-run)")

    ctx: Dict[str, Any] = {
        "config": config,
        "args": args,
        "run_dir": run_dir,
    }

    try:
        stats = run_pipeline(ctx)
    except PipelineAbortError as e:
        logger.error("Pipeline aborted: %s", e)
        return 1

    if stats and run_dir:
        logger.info("Filter completed. Output in: %s", run_dir)
        for line in json.dumps(stats.get("outputs", {}), ensure_ascii=False).splitlines():
            logger.info("  %s", line)
    elif args.dry_run:
        messages = ctx.get("messages", [])
        logger.info("DRY RUN: Parsed %d messages", len(messages))

    return 0


if __name__ == "__main__":
    sys.exit(main())
