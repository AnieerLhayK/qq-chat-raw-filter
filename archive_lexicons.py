#!/usr/bin/env python3
"""
archive_lexicons.py — One-shot migration: archive current lexicons to lexicons/archive/

Reads each current lexicon file, enriches entries with archival metadata,
and writes enriched copies to lexicons/archive/.  The active lexicons stay
in place — this is a parallel copy, not a move.

Usage:
  python archive_lexicons.py                 # create archive (skips if exists)
  python archive_lexicons.py --force          # overwrite existing archive
  python archive_lexicons.py --dry-run        # preview only
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

LEXICON_DIR = Path("D:/AI/raw_material/qq/exports/lexicons")
ARCHIVE_DIR = LEXICON_DIR / "archive"

# Map: source filename → (archive filename, old_category factory, enrichment_fields)
# old_category factory is a function: (entry: dict) -> str
ARCHIVE_MANIFEST: Dict[str, Dict[str, Any]] = {
    "phrase_bank.jsonl": {
        "archive_name": "phrase_bank_archive.jsonl",
        "old_category": lambda e: f"style_markers.{e.get('label', 'unknown')}",
        "description": "Style markers — analysis, tone, interruption, argument, tag markers",
    },
    "chaos_lexicon.jsonl": {
        "archive_name": "chaos_lexicon_archive.jsonl",
        "old_category": lambda e: f"chaos.{e.get('severity', 'unknown')}",
        "description": "Chaos / slang / profanity words — mild and strong severity",
    },
    "privacy_lexicon.jsonl": {
        "archive_name": "privacy_lexicon_archive.jsonl",
        "old_category": lambda e: f"privacy.{e.get('risk', 'unknown')}",
        "description": "Privacy risk words — high and medium risk levels",
    },
    "drop_sentence_words.jsonl": {
        "archive_name": "drop_sentence_words_archive.jsonl",
        "old_category": lambda e: "filter.drop_sentence",
        "description": "Trigger words for dropping entire sentence blocks",
    },
    "mask_words.jsonl": {
        "archive_name": "mask_words_archive.jsonl",
        "old_category": lambda e: "filter.mask",
        "description": "Words to mask/replace in output blocks",
    },
    "phrase_candidates.jsonl": {
        "archive_name": "phrase_candidates_archive.jsonl",
        "old_category": lambda e: "phrase_mining.candidates",
        "description": "Auto-discovered phrase candidates from phrase mining",
    },
    "phrase_stoplist.txt": {
        "archive_name": "phrase_stoplist_archive.txt",
        "old_category": "phrase_mining.stoplist",
        "description": "Stoplist — high-frequency low-value phrases to filter out",
        "is_text": True,
    },
    "manual_keep.jsonl": {
        "archive_name": "manual_keep_archive.jsonl",
        "old_category": lambda e: "manual.keep",
        "description": "Manually specified phrases to always keep",
    },
    "manual_drop.jsonl": {
        "archive_name": "manual_drop_archive.jsonl",
        "old_category": lambda e: "manual.drop",
        "description": "Manually specified phrases to always drop",
    },
}


def _enrich_entry(entry: Dict[str, Any], source_name: str,
                  manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Add archive metadata fields to a single lexicon entry."""
    factory = manifest.get("old_category", lambda e: "unknown")
    old_cat = factory(entry) if callable(factory) else str(factory)

    enriched = dict(entry)
    enriched["old_category"] = old_cat
    enriched["archived_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    enriched["participates_in_matching"] = False
    enriched["source_file"] = source_name
    return enriched


def archive_jsonl(source_path: Path, archive_path: Path,
                  manifest: Dict[str, Any]) -> int:
    """Read JSONL, enrich each entry, write to archive. Returns entry count."""
    entries: List[Dict[str, Any]] = []
    with source_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"  WARN: Skipping invalid JSON in {source_path.name}: {e}")
                continue
            entries.append(entry)

    enriched = [_enrich_entry(e, source_path.name, manifest) for e in entries]

    with archive_path.open("w", encoding="utf-8") as f:
        for e in enriched:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    return len(enriched)


def archive_text(source_path: Path, archive_path: Path,
                 manifest: Dict[str, Any]) -> int:
    """Copy text file with archive metadata header. Returns line count."""
    old_cat = manifest.get("old_category", "unknown")
    if callable(old_cat):
        old_cat = "unknown"

    migrated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    lines = source_path.read_text(encoding="utf-8").splitlines()
    content_lines = [l for l in lines if l.strip()]

    header = [
        f"# Archive of: {source_path.name}",
        f"# Migrated at: {migrated_at}",
        f"# Old category: {old_cat}",
        f"# Participates in matching: false",
        f"# This file is read-only historical reference.",
        f"# The active stoplist lives at lexicons/phrase_stoplist.txt",
        f"",
    ]

    with archive_path.open("w", encoding="utf-8") as f:
        f.write("\n".join(header))
        f.write("\n".join(content_lines))
        if content_lines:
            f.write("\n")

    return len(content_lines)


def generate_archive_readme(results: Dict[str, Any]) -> str:
    """Generate README.md content for the archive directory."""
    total_entries = sum(r["entries"] for r in results.values())
    lines = [
        "# Lexicon Archive",
        "",
        f"Archived at: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"Total archived entries: {total_entries}",
        "",
        "## Purpose",
        "",
        "This directory contains the **original hand-curated lexicons** archived",
        "as historical reference. These files are **read-only** and do NOT",
        "participate in active matching.",
        "",
        "The active lexicons live in the parent directory (`lexicons/*.jsonl`).",
        "",
        "## Archive vs Active",
        "",
        "| Archive | Active |",
        "|---------|--------|",
        "| Historical snapshot | Data-verified working set |",
        "| Read-only | Updated by audit + phrase mining |",
        "| Full original词库 | Subset verified by frequency data |",
        "| `participates_in_matching: false` | Used in pipeline scoring/matching |",
        "",
        "## Workflow",
        "",
        "1. Archive preserves the original hand-curated词库 (these files).",
        "2. Pipeline runs include a frequency audit stage that checks each",
        "   archived word against real data across all buckets.",
        "3. `generate_active_lexicon.py` combines audit results with phrase",
        "   mining discoveries to regenerate active lexicons.",
        "4. Active lexicons get refined over time; archive stays unchanged.",
        "",
        "## Files",
        "",
        "| File | Entries | Description |",
        "|------|---------|-------------|",
    ]

    for source_name, info in sorted(results.items()):
        if source_name == "skipped":
            continue
        lines.append(
            f"| `{info['archive_name']}` "
            f"| {info['entries']} "
            f"| {info['description']} |"
        )

    lines.append("")
    lines.append("## Schema")
    lines.append("")
    lines.append("Every archived JSONL entry has these additional fields:")
    lines.append("")
    lines.append("- `old_category`: original classification (e.g. `style_markers.analysis_marker`)")
    lines.append("- `archived_at`: ISO-8601 timestamp of archive creation")
    lines.append("- `participates_in_matching`: always `false` for archive entries")
    lines.append("- `source_file`: original filename this entry came from")
    lines.append("")
    lines.append("Original fields (`phrase`, `label`, `source`, `status`, `severity`, `risk`, etc.)")
    lines.append("are preserved exactly as they were at migration time.")

    return "\n".join(lines) + "\n"


def run_archive(force: bool = False, dry_run: bool = False) -> int:
    """Main archive logic. Returns exit code."""
    if not ARCHIVE_DIR.exists():
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        print(f"Created: {ARCHIVE_DIR}")

    results: Dict[str, Any] = {}
    skipped = 0
    errors = 0

    for source_name, manifest in ARCHIVE_MANIFEST.items():
        source_path = LEXICON_DIR / source_name
        archive_name = manifest["archive_name"]
        archive_path = ARCHIVE_DIR / archive_name

        if not source_path.exists():
            print(f"  WARN: Source not found, skipping: {source_name}")
            skipped += 1
            continue

        if archive_path.exists() and not force:
            print(f"  SKIP Already exists (use --force to overwrite): {archive_name}")
            skipped += 1
            # Count existing entries for stats
            if manifest.get("is_text"):
                content = archive_path.read_text(encoding="utf-8")
                count = len([l for l in content.splitlines()
                            if l.strip() and not l.strip().startswith("#")])
            else:
                count = sum(1 for _ in archive_path.open("r", encoding="utf-8"))
            results[source_name] = {
                "archive_name": archive_name,
                "entries": count,
                "description": manifest["description"],
                "status": "already_exists",
            }
            continue

        if dry_run:
            print(f"  -> Would archive: {source_name} -> {archive_name}")
            # Count entries
            if manifest.get("is_text"):
                content = source_path.read_text(encoding="utf-8")
                count = len([l for l in content.splitlines() if l.strip()])
            else:
                count = sum(1 for ln in source_path.open("r", encoding="utf-8")
                           if ln.strip())
            results[source_name] = {
                "archive_name": archive_name,
                "entries": count,
                "description": manifest["description"],
                "status": "would_create",
            }
            continue

        try:
            if manifest.get("is_text"):
                count = archive_text(source_path, archive_path, manifest)
            else:
                count = archive_jsonl(source_path, archive_path, manifest)

            status = "overwritten" if archive_path.exists() else "created"
            print(f"  OK {status}: {archive_name} ({count} entries)")
            results[source_name] = {
                "archive_name": archive_name,
                "entries": count,
                "description": manifest["description"],
                "status": status,
            }
        except Exception as e:
            print(f"  FAILED: {source_name}: {e}")
            errors += 1

    # Generate README
    readme_path = ARCHIVE_DIR / "README.md"
    if not dry_run:
        readme_content = generate_archive_readme(results)
        readme_path.write_text(readme_content, encoding="utf-8")
        print(f"\n  OK Archive README: {readme_path}")

    # Summary
    total_entries = sum(r["entries"] for r in results.values())
    created = sum(1 for r in results.values() if r["status"] in ("created", "overwritten"))
    print(f"\nArchive {'preview' if dry_run else 'complete'}: "
          f"{created} files, {total_entries} total entries, "
          f"{skipped} skipped, {errors} errors")

    return 0 if errors == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Archive current lexicons to lexicons/archive/ with metadata")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite existing archive files")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview only, don't write files")
    args = parser.parse_args()

    print(f"Lexicon Archive Migration")
    print(f"  Source: {LEXICON_DIR}")
    print(f"  Target: {ARCHIVE_DIR}")
    print()

    return run_archive(force=args.force, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
