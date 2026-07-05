#!/usr/bin/env python3
"""
generate_active_lexicon.py — Regenerate active lexicon files from audit results.

Reads frequency audit output + archive lexicons + phrase mining results, then
generates new active lexicon files combining:
  1. Archived words verified by frequency audit (promote_to_active)
  2. High-keyness new phrases from phrase mining
  3. Low-frequency words demoted to candidates or removed

Usage:
  # Preview only (writes to generated_lexicons/):
  python generate_active_lexicon.py --audit run_20260705_120000/phrase_mining/legacy_lexicon_frequency_audit.jsonl

  # Actually overwrite active lexicons:
  python generate_active_lexicon.py --audit <path> --force

  # Custom output directory:
  python generate_active_lexicon.py --audit <path> --output-dir lexicons/active_v2/
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# Default paths
SCRIPT_DIR = Path(__file__).resolve().parent
LEXICON_DIR = SCRIPT_DIR / "lexicons"
ARCHIVE_DIR = LEXICON_DIR / "archive"


def load_audit(audit_path: Path) -> List[Dict[str, Any]]:
    """Load frequency audit JSONL file."""
    entries: List[Dict[str, Any]] = []
    with audit_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def load_archive_entries() -> Dict[str, List[Dict[str, Any]]]:
    """Load all archive JSONL files, keyed by source file name."""
    archive_entries: Dict[str, List[Dict[str, Any]]] = {}
    archive_map = {
        "phrase_bank.jsonl": ARCHIVE_DIR / "phrase_bank_archive.jsonl",
        "chaos_lexicon.jsonl": ARCHIVE_DIR / "chaos_lexicon_archive.jsonl",
        "privacy_lexicon.jsonl": ARCHIVE_DIR / "privacy_lexicon_archive.jsonl",
        "drop_sentence_words.jsonl": ARCHIVE_DIR / "drop_sentence_words_archive.jsonl",
        "mask_words.jsonl": ARCHIVE_DIR / "mask_words_archive.jsonl",
        "phrase_candidates.jsonl": ARCHIVE_DIR / "phrase_candidates_archive.jsonl",
        "manual_keep.jsonl": ARCHIVE_DIR / "manual_keep_archive.jsonl",
        "manual_drop.jsonl": ARCHIVE_DIR / "manual_drop_archive.jsonl",
    }

    for name, path in archive_map.items():
        if not path.exists():
            continue
        entries: List[Dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        if entries:
            archive_entries[name] = entries

    # Also load stoplist archive
    stoplist_path = ARCHIVE_DIR / "phrase_stoplist_archive.txt"
    if stoplist_path.exists():
        stopwords: List[Dict[str, Any]] = []
        content = stoplist_path.read_text(encoding="utf-8")
        for line in content.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                stopwords.append({"phrase": line, "source": "archived_stoplist"})
        if stopwords:
            archive_entries["phrase_stoplist.txt"] = stopwords

    return archive_entries


def load_phrase_candidates(run_dir: Optional[Path]) -> List[Dict[str, Any]]:
    """Load phrase mining candidates from a run's phrase_mining/ directory."""
    if not run_dir:
        return []
    pm_dir = run_dir / "phrase_mining"
    candidates_path = pm_dir / "phrase_candidates.jsonl"
    if not candidates_path.exists():
        return []
    entries: List[Dict[str, Any]] = []
    with candidates_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def clean_entry(entry: Dict[str, Any], new_source: str) -> Dict[str, Any]:
    """Remove archive metadata fields, set new source."""
    cleaned = {}
    # Core fields to keep
    keep_fields = {"phrase", "label", "source", "status", "allowed_in_skill",
                   "severity", "risk", "score", "max_usage"}
    for k in keep_fields:
        if k in entry:
            cleaned[k] = entry[k]
    # Override source
    cleaned["source"] = new_source
    cleaned["status"] = "active_verified"
    return cleaned


def generate_active(
    audit_entries: List[Dict[str, Any]],
    archive_entries: Dict[str, List[Dict[str, Any]]],
    phrase_candidates: List[Dict[str, Any]],
    output_dir: Path,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Generate new active lexicon files.

    Returns stats dict with counts of what was done.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Build lookup: phrase -> audit result
    audit_map: Dict[str, Dict[str, Any]] = {}
    for e in audit_entries:
        audit_map[e["term"]] = e

    # Identify promoted phrases
    promoted_phrases: Set[str] = set()
    for e in audit_entries:
        if e["suggested_status"] in ("promote_to_active", "keep_in_privacy_lexicon",
                                      "keep_in_chaos_lexicon", "keep_in_stoplist",
                                      "verified_filter_word"):
            promoted_phrases.add(e["term"])
        elif e["suggested_status"] == "keep_as_candidate":
            # Also allow candidate-level words to stay if they have some data
            if e["freq_total"] >= 3:
                promoted_phrases.add(e["term"])

    stats: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dry_run": dry_run,
        "output_dir": str(output_dir),
        "audit_entries": len(audit_entries),
        "promoted_from_archive": len(promoted_phrases),
        "by_file": {},
        "phrase_mining_new": 0,
        "candidates_written": 0,
        "demoted_written": 0,
    }

    # --- Generate each active lexicon file ---

    # 1. phrase_bank.jsonl — promoted style words + high-keyness new phrases
    phrase_bank: List[Dict[str, Any]] = []
    if "phrase_bank.jsonl" in archive_entries:
        for entry in archive_entries["phrase_bank.jsonl"]:
            phrase = entry.get("phrase", "")
            if phrase in promoted_phrases:
                cleaned = clean_entry(entry, "frequency_verified")
                phrase_bank.append(cleaned)

    # Add high-keyness new phrases from mining
    for pc in phrase_candidates:
        if pc.get("style_keyness", 0) >= 2.0 and pc.get("freq", 0) >= 5:
            label = "mined_style_phrase"
            if pc.get("chaos_rate", 0) > 0.5:
                continue  # chaos-heavy phrases go to chaos lexicon
            if pc.get("privacy_bucket_rate", 0) > 0.3:
                continue  # privacy-heavy phrases handled separately
            phrase_bank.append({
                "phrase": pc["phrase"],
                "label": label,
                "source": "phrase_mining_auto",
                "status": "auto_candidate",
                "allowed_in_skill": True,
            })
            stats["phrase_mining_new"] += 1

    _write_jsonl_if(phrase_bank, output_dir / "phrase_bank.jsonl", dry_run)
    stats["by_file"]["phrase_bank.jsonl"] = len(phrase_bank)

    # 2. chaos_lexicon.jsonl — verified chaos words + new chaos phrases
    chaos_lexicon: List[Dict[str, Any]] = []
    if "chaos_lexicon.jsonl" in archive_entries:
        for entry in archive_entries["chaos_lexicon.jsonl"]:
            phrase = entry.get("phrase", "")
            if phrase in promoted_phrases:
                cleaned = clean_entry(entry, "frequency_verified")
                chaos_lexicon.append(cleaned)

    # Add new chaos phrases
    for pc in phrase_candidates:
        if pc.get("chaos_rate", 0) > 0.5 and pc.get("freq", 0) >= 3:
            severity = "strong" if pc.get("chaos_rate", 0) > 0.8 else "mild"
            chaos_lexicon.append({
                "phrase": pc["phrase"],
                "severity": severity,
                "source": "phrase_mining_auto",
                "status": "auto_candidate",
                "allowed_in_skill": severity == "mild",
                "max_usage": "medium" if severity == "mild" else "none",
            })
            stats["phrase_mining_new"] += 1

    _write_jsonl_if(chaos_lexicon, output_dir / "chaos_lexicon.jsonl", dry_run)
    stats["by_file"]["chaos_lexicon.jsonl"] = len(chaos_lexicon)

    # 3. privacy_lexicon.jsonl — verified privacy words
    privacy_lexicon: List[Dict[str, Any]] = []
    if "privacy_lexicon.jsonl" in archive_entries:
        for entry in archive_entries["privacy_lexicon.jsonl"]:
            phrase = entry.get("phrase", "")
            audit = audit_map.get(phrase, {})
            if audit.get("suggested_status") in ("keep_in_privacy_lexicon", "promote_to_active"):
                cleaned = clean_entry(entry, "frequency_verified")
                privacy_lexicon.append(cleaned)
            elif audit.get("freq_total", 0) >= 1:
                # Still seen in data, keep in privacy lexicon
                cleaned = clean_entry(entry, "frequency_verified_low")
                privacy_lexicon.append(cleaned)

    # Add privacy-heavy new phrases
    for pc in phrase_candidates:
        if pc.get("privacy_bucket_rate", 0) > 0.3 and pc.get("freq", 0) >= 3:
            privacy_lexicon.append({
                "phrase": pc["phrase"],
                "risk": "medium",
                "source": "phrase_mining_auto",
                "status": "auto_candidate",
                "score": 1.5,
            })
            stats["phrase_mining_new"] += 1

    _write_jsonl_if(privacy_lexicon, output_dir / "privacy_lexicon.jsonl", dry_run)
    stats["by_file"]["privacy_lexicon.jsonl"] = len(privacy_lexicon)

    # 4. drop_sentence_words.jsonl — verified filter words
    drop_words: List[Dict[str, Any]] = []
    if "drop_sentence_words.jsonl" in archive_entries:
        for entry in archive_entries["drop_sentence_words.jsonl"]:
            phrase = entry.get("phrase", "")
            if phrase in promoted_phrases:
                cleaned = clean_entry(entry, "frequency_verified")
                drop_words.append(cleaned)
    _write_jsonl_if(drop_words, output_dir / "drop_sentence_words.jsonl", dry_run)
    stats["by_file"]["drop_sentence_words.jsonl"] = len(drop_words)

    # 5. mask_words.jsonl — verified mask words
    mask_words: List[Dict[str, Any]] = []
    if "mask_words.jsonl" in archive_entries:
        for entry in archive_entries["mask_words.jsonl"]:
            phrase = entry.get("phrase", "")
            if phrase in promoted_phrases:
                cleaned = clean_entry(entry, "frequency_verified")
                mask_words.append(cleaned)
    _write_jsonl_if(mask_words, output_dir / "mask_words.jsonl", dry_run)
    stats["by_file"]["mask_words.jsonl"] = len(mask_words)

    # 6. phrase_stoplist.txt — high-frequency low-value words
    stoplist: Set[str] = set()
    if "phrase_stoplist.txt" in archive_entries:
        for entry in archive_entries["phrase_stoplist.txt"]:
            phrase = entry.get("phrase", "")
            audit = audit_map.get(phrase, {})
            if audit.get("suggested_status") == "keep_in_stoplist":
                stoplist.add(phrase)
            elif audit.get("freq_total", 0) >= 10 and audit.get("style_keyness", 0) < 1.0:
                stoplist.add(phrase)

    # Add high-frequency low-PMI phrases from mining
    for pc in phrase_candidates:
        if (pc.get("freq", 0) >= 10 and pc.get("min_pmi", 0) < 1.5
                and pc.get("style_keyness", 0) < 1.0):
            stoplist.add(pc["phrase"])

    _write_text_if(sorted(stoplist), output_dir / "phrase_stoplist.txt", dry_run)
    stats["by_file"]["phrase_stoplist.txt"] = len(stoplist)

    # 7. phrase_candidates.jsonl — new phrases + demoted archive words
    candidates: List[Dict[str, Any]] = []
    for pc in phrase_candidates:
        if pc.get("style_keyness", 0) >= 1.0 and pc.get("freq", 0) >= 3:
            candidates.append(pc)
    # Add demoted archive words
    for e in audit_entries:
        if e["suggested_status"] == "keep_as_candidate":
            candidates.append({
                "phrase": e["term"],
                "source": "frequency_audit_demotion",
                "status": "demoted_from_archive",
                "old_category": e["old_category"],
                "freq_total": e["freq_total"],
                "style_keyness": e["style_keyness"],
                "reasons": e.get("reasons", []),
            })
            stats["candidates_written"] += 1

    _write_jsonl_if(candidates, output_dir / "phrase_candidates.jsonl", dry_run)
    stats["by_file"]["phrase_candidates.jsonl"] = len(candidates)

    # 8. manual_keep.jsonl — keep as-is
    if "manual_keep.jsonl" in archive_entries:
        keep = [clean_entry(e, "frequency_verified") for e in archive_entries["manual_keep.jsonl"]]
        _write_jsonl_if(keep, output_dir / "manual_keep.jsonl", dry_run)
        stats["by_file"]["manual_keep.jsonl"] = len(keep)

    # 9. manual_drop.jsonl — keep as-is
    if "manual_drop.jsonl" in archive_entries:
        drop = [clean_entry(e, "frequency_verified") for e in archive_entries["manual_drop.jsonl"]]
        _write_jsonl_if(drop, output_dir / "manual_drop.jsonl", dry_run)
        stats["by_file"]["manual_drop.jsonl"] = len(drop)

    # 10. lexicon_removed_or_demoted.jsonl — tracking what was removed
    demoted: List[Dict[str, Any]] = []
    for e in audit_entries:
        if e["suggested_status"] == "demote_or_remove":
            demoted.append({
                "phrase": e["term"],
                "old_category": e["old_category"],
                "source_file": e["source_file"],
                "freq_total": e["freq_total"],
                "reasons": e.get("reasons", []),
            })
    _write_jsonl_if(demoted, output_dir / "lexicon_removed_or_demoted.jsonl", dry_run)
    stats["demoted_written"] = len(demoted)
    stats["by_file"]["lexicon_removed_or_demoted.jsonl"] = len(demoted)

    return stats


def generate_report(stats: Dict[str, Any], output_dir: Path) -> None:
    """Generate active_lexicon_report.md."""
    lines: List[str] = []
    lines.append("# Active Lexicon Generation Report")
    lines.append("")
    lines.append(f"Generated at: {stats['generated_at']}")
    lines.append(f"Mode: {'DRY RUN (preview only)' if stats['dry_run'] else 'LIVE (files written)'}")
    lines.append(f"Output directory: {stats['output_dir']}")
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Audit entries processed: {stats['audit_entries']}")
    lines.append(f"- Words promoted from archive: {stats['promoted_from_archive']}")
    lines.append(f"- New phrases from mining: {stats['phrase_mining_new']}")
    lines.append(f"- Words demoted to candidates: {stats['candidates_written']}")
    lines.append(f"- Words removed/demoted: {stats['demoted_written']}")
    lines.append("")

    lines.append("## Generated Active Lexicon Files")
    lines.append("")
    lines.append("| File | Entries |")
    lines.append("|------|---------|")
    for fname, count in sorted(stats.get("by_file", {}).items()):
        lines.append(f"| {fname} | {count} |")
    lines.append("")

    lines.append("## Next Steps")
    lines.append("")
    if stats["dry_run"]:
        lines.append(f"1. Review the generated files in `{stats['output_dir']}`.")
        lines.append("2. Run with `--force` to overwrite active lexicons.")
    else:
        lines.append("1. Active lexicons have been updated.")
        lines.append("2. Run the pipeline to verify new lexicons work correctly.")
        lines.append("3. Review `lexicon_removed_or_demoted.jsonl` for any false removals.")
    lines.append("")

    report_path = output_dir / "active_lexicon_report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  Report: {report_path}")


def _write_jsonl_if(entries: List[Dict[str, Any]], path: Path, dry_run: bool) -> None:
    """Write JSONL file unless dry_run."""
    if dry_run:
        print(f"  [DRY RUN] Would write {len(entries)} entries to {path.name}")
        return
    with path.open("w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"  Wrote {len(entries)} entries to {path.name}")


def _write_text_if(lines: List[str], path: Path, dry_run: bool) -> None:
    """Write text file (one line per entry) unless dry_run."""
    if dry_run:
        print(f"  [DRY RUN] Would write {len(lines)} lines to {path.name}")
        return
    path.write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8")
    print(f"  Wrote {len(lines)} lines to {path.name}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate active lexicon files from frequency audit results")
    parser.add_argument("--audit", required=True,
                        help="Path to legacy_lexicon_frequency_audit.jsonl")
    parser.add_argument("--output-dir", default=None,
                        help="Output directory (default: lexicons/generated/ or lexicons/ with --force)")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite active lexicons in lexicons/ directory")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview only, do not write files")
    args = parser.parse_args()

    audit_path = Path(args.audit)
    if not audit_path.exists():
        print(f"ERROR: Audit file not found: {audit_path}")
        return 1

    # Determine output directory
    if args.output_dir:
        output_dir = Path(args.output_dir)
    elif args.force:
        output_dir = LEXICON_DIR
    else:
        output_dir = LEXICON_DIR / "generated"

    is_dry_run = args.dry_run or not args.force

    print("Active Lexicon Generator")
    print(f"  Audit file: {audit_path}")
    print(f"  Output dir: {output_dir}")
    print(f"  Mode: {'DRY RUN' if is_dry_run else 'LIVE (--force)'}")
    print()

    # Load data
    print("Loading data...")
    audit_entries = load_audit(audit_path)
    print(f"  Audit entries: {len(audit_entries)}")

    archive_entries = load_archive_entries()
    print(f"  Archive files: {len(archive_entries)}")

    # Try to find phrase mining results in the same run
    run_dir = audit_path.parent.parent if audit_path.parent.name == "phrase_mining" else None
    phrase_candidates = load_phrase_candidates(run_dir)
    print(f"  Phrase mining candidates: {len(phrase_candidates)}")
    print()

    # Generate
    print("Generating active lexicons...")
    stats = generate_active(
        audit_entries, archive_entries, phrase_candidates,
        output_dir, dry_run=is_dry_run,
    )

    # Report
    generate_report(stats, output_dir)

    total_active = sum(
        v for k, v in stats.get("by_file", {}).items()
        if not k.startswith("lexicon_removed")
    )
    print(f"\nDone. {total_active} total active entries across {len(stats['by_file'])} files.")
    if is_dry_run:
        print("This was a DRY RUN. Use --force to actually write to lexicons/.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
