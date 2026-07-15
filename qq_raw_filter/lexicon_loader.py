"""
lexicon_loader.py — Load external lexicon files (JSONL, TXT) referenced by TOML config.

Transforms the file-based lexicons into in-memory data structures used by the
scorer, style_tagger, and phrase_miner modules.  All paths are resolved
relative to AI_ROOT.

Lifecycle (one-shot per pipeline run):
  lexicon = load_lexicons(config, ai_root)
  lexicon["phrase_bank"]          # list of dicts
  lexicon["chaos_lexicon"]        # list of dicts
  lexicon["privacy_lexicon"]      # list of dicts
  lexicon["phrase_stoplist"]      # set of strings
  lexicon["stoplist_diagnostics"] # source duplicates/conflicts for maintenance
  lexicon["phrase_candidates"]    # list of dicts
  lexicon["drop_sentence_words"]  # list of dicts
  lexicon["mask_words"]           # list of dicts
  lexicon["manual_keep"]          # list of dicts
  lexicon["manual_drop"]          # list of dicts
  lexicon["by_label"]             # {label: [phrase_dict, ...]}
  lexicon["analysis_markers"]     # shorthand: list of strings
  lexicon["tone_markers"]         # shorthand: list of strings
  lexicon["light_interruption"]   # shorthand: list of strings
  lexicon["argument_markers"]     # shorthand: list of strings
  lexicon["analysis_tag_markers"] # shorthand: list of strings
  lexicon["mild_words"]           # shorthand: list of strings
  lexicon["strong_words"]         # shorthand: list of strings
  lexicon["high_risk_words"]      # shorthand: list of strings
  lexicon["medium_risk_words"]    # shorthand: list of strings
  lexicon["drop_sentence_phrases"] # shorthand: list of strings
  lexicon["mask_phrases"]         # shorthand: list of strings
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from qq_raw_filter.review_feedback import load_review_decisions

logger = logging.getLogger(__name__)


def _resolve_path(raw: str, ai_root: Path) -> Path:
    """Resolve a path relative to AI_ROOT if it is not absolute."""
    p = Path(raw)
    return p if p.is_absolute() else (ai_root / p)


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    """Load a JSONL file; return empty list if missing."""
    if not path.exists():
        logger.warning("Lexicon file not found, using empty: %s", path)
        return []
    entries: List[Dict[str, Any]] = []
    # ``phrase_candidates.jsonl`` may be produced by tools that emit a UTF-8
    # BOM.  ``utf-8-sig`` consumes it on the first line without changing any
    # subsequent entries, preventing a valid candidate from being dropped.
    with path.open("r", encoding="utf-8-sig") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError as e:
                logger.warning("Skipping invalid JSONL line %d in %s: %s", lineno, path, e)
    logger.info("Loaded %d entries from %s", len(entries), path)
    return entries


def _parse_stoplist_line(line: str) -> str:
    """Parse one stoplist line, returning empty for comments/blanks."""
    w = line.strip()
    if not w or w.startswith("#"):
        return ""
    if " #" in w:
        w = w[:w.index(" #")].strip()
    return w


def _load_stoplist_entries(path: Path) -> List[str]:
    """Load a stoplist TXT file preserving order and duplicates.

    Lines starting with ``#`` are treated as comments and skipped.
    Trailing ``# comments`` on data lines are also stripped.
    """
    if not path.exists():
        logger.warning("Stoplist not found, using empty: %s", path)
        return []
    words: List[str] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            w = _parse_stoplist_line(line)
            if w:
                words.append(w)
    return words


def _load_stoplist(path: Path) -> Set[str]:
    """Load a stoplist TXT file as the runtime de-duplicated set."""
    words = set(_load_stoplist_entries(path))
    logger.info("Loaded %d stopwords from %s", len(words), path)
    return words


def diagnose_stoplist(path: Path, phrase_bank: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Diagnose stoplist maintenance issues without changing runtime behavior."""
    entries = _load_stoplist_entries(path)
    counts = Counter(entries)
    duplicates = [
        {"phrase": phrase, "count": count}
        for phrase, count in sorted(counts.items())
        if count > 1
    ]

    phrase_labels = {
        str(entry.get("phrase", "")).strip(): entry.get("label", "unlabeled")
        for entry in phrase_bank
        if str(entry.get("phrase", "")).strip()
    }
    conflicts = [
        {"phrase": phrase, "label": phrase_labels[phrase]}
        for phrase in sorted(set(entries) & set(phrase_labels))
    ]

    per_length: Dict[str, int] = {}
    for phrase in entries:
        bucket = "5plus" if len(phrase) >= 5 else str(len(phrase))
        per_length[bucket] = per_length.get(bucket, 0) + 1

    return {
        "total_entries": len(entries),
        "unique_entries": len(counts),
        "duplicates": duplicates,
        "conflicts_with_phrase_bank": conflicts,
        "conflict_categories": dict(sorted(Counter(c["label"] for c in conflicts).items())),
        "per_length": dict(sorted(per_length.items())),
    }


def load_lexicons(config: Dict[str, Any], ai_root: Path) -> Dict[str, Any]:
    """Load all lexicon files registered in config[lexicon].

    Returns a dict with structured accessors.
    """
    lex_cfg = config.get("lexicon", {})
    result: Dict[str, Any] = {}

    # --- Raw file contents ---
    result["phrase_bank"] = _load_jsonl(
        _resolve_path(lex_cfg.get("phrase_bank_path", ""), ai_root)
    )
    result["phrase_candidates"] = _load_jsonl(
        _resolve_path(lex_cfg.get("phrase_candidates_path", ""), ai_root)
    )
    result["chaos_lexicon"] = _load_jsonl(
        _resolve_path(lex_cfg.get("chaos_lexicon_path", ""), ai_root)
    )
    result["privacy_lexicon"] = _load_jsonl(
        _resolve_path(lex_cfg.get("privacy_lexicon_path", ""), ai_root)
    )
    result["drop_sentence_words"] = _load_jsonl(
        _resolve_path(lex_cfg.get("drop_sentence_words_path", ""), ai_root)
    )
    result["mask_words"] = _load_jsonl(
        _resolve_path(lex_cfg.get("mask_words_path", ""), ai_root)
    )
    result["manual_keep"] = _load_jsonl(
        _resolve_path(lex_cfg.get("manual_keep_path", ""), ai_root)
    )
    result["manual_drop"] = _load_jsonl(
        _resolve_path(lex_cfg.get("manual_drop_path", ""), ai_root)
    )
    review_cfg = config.get("review", {})
    result["review_decisions"] = load_review_decisions(
        _resolve_path(review_cfg.get("decisions_path", ""), ai_root)
    )
    manual_keep_phrases = {
        str(e.get("phrase", "")).strip() for e in result["manual_keep"]
        if str(e.get("phrase", "")).strip()
    }
    manual_drop_phrases = {
        str(e.get("phrase", "")).strip() for e in result["manual_drop"]
        if str(e.get("phrase", "")).strip()
    }
    for phrase, entry in result["review_decisions"]["phrase"].items():
        if entry.get("decision") == "keep":
            manual_drop_phrases.discard(phrase)
            manual_keep_phrases.add(phrase)
        else:
            manual_keep_phrases.discard(phrase)
            manual_drop_phrases.add(phrase)
    # A confirmed phrase drop must not keep inflating style scores through a
    # stale phrase_bank entry.
    result["phrase_bank"] = [
        entry for entry in result["phrase_bank"]
        if str(entry.get("phrase", "")).strip() not in manual_drop_phrases
    ]
    result["manual_keep_phrases"] = manual_keep_phrases
    result["manual_drop_phrases"] = manual_drop_phrases
    stoplist_path = _resolve_path(lex_cfg.get("phrase_stoplist_path", ""), ai_root)
    result["phrase_stoplist"] = _load_stoplist(stoplist_path)
    result["stoplist_diagnostics"] = diagnose_stoplist(
        stoplist_path, result["phrase_bank"]
    )
    if result["stoplist_diagnostics"]["duplicates"]:
        logger.warning(
            "Stoplist has %d duplicate source entries; runtime matching is unaffected",
            len(result["stoplist_diagnostics"]["duplicates"]),
        )
    if result["stoplist_diagnostics"]["conflicts_with_phrase_bank"]:
        logger.warning(
            "Stoplist overlaps phrase_bank on %d phrases; review lexicon intent",
            len(result["stoplist_diagnostics"]["conflicts_with_phrase_bank"]),
        )

    # --- By-label index ---
    by_label: Dict[str, List[Dict[str, Any]]] = {}
    for entry in result["phrase_bank"]:
        label = entry.get("label", "unlabeled")
        by_label.setdefault(label, []).append(entry)
    for entry in result["manual_keep"]:
        label = entry.get("label", "manual_keep")
        by_label.setdefault(label, []).append(entry)
    result["by_label"] = by_label

    # --- Shorthand string lists (for backward compat with scorer / tagger) ---
    result["analysis_markers"] = [
        e["phrase"] for e in by_label.get("analysis_marker", [])
    ]
    result["tone_markers"] = [
        e["phrase"] for e in by_label.get("tone_marker", [])
    ]
    result["light_interruption"] = [
        e["phrase"] for e in by_label.get("light_interruption", [])
    ]
    result["argument_markers"] = [
        e["phrase"] for e in by_label.get("argument_marker", [])
    ]
    result["analysis_tag_markers"] = [
        e["phrase"] for e in by_label.get("analysis_tag_marker", [])
    ]

    # Chaos by severity
    result["mild_words"] = [
        e["phrase"] for e in result["chaos_lexicon"]
        if e.get("severity") == "mild"
    ]
    result["strong_words"] = [
        e["phrase"] for e in result["chaos_lexicon"]
        if e.get("severity") == "strong"
    ]

    # Privacy by risk
    result["high_risk_words"] = [
        e["phrase"] for e in result["privacy_lexicon"]
        if e.get("risk") == "high"
    ]
    result["medium_risk_words"] = [
        e["phrase"] for e in result["privacy_lexicon"]
        if e.get("risk") == "medium"
    ]

    # Filter lists — flat string lists for drop_sentence_words / mask_words
    result["drop_sentence_phrases"] = [
        e["phrase"] for e in result["drop_sentence_words"]
    ]
    result["mask_phrases"] = [
        e["phrase"] for e in result["mask_words"]
    ]

    # Also keep the original TOML inline lists as fallback (for filter lists
    # that are small enough to keep in TOML).
    result["_fallback_analysis"] = config.get("style_markers", {}).get("analysis", [])
    result["_fallback_tone"] = config.get("style_markers", {}).get("tone", [])

    logger.info(
        "Lexicon loaded: %d phrase_bank, %d chaos, %d privacy, %d stopwords, "
        "%d candidates, %d drop_sentence, %d mask_words",
        len(result["phrase_bank"]),
        len(result["chaos_lexicon"]),
        len(result["privacy_lexicon"]),
        len(result["phrase_stoplist"]),
        len(result["phrase_candidates"]),
        len(result["drop_sentence_words"]),
        len(result["mask_words"]),
    )
    return result


# ---------------------------------------------------------------------------
# Archive lexicon loading (v0.3)
# ---------------------------------------------------------------------------
def load_archive_terms(config: Dict[str, Any], ai_root: Path) -> List[Dict[str, Any]]:
    """Load all archived lexicon entries as a flat list for frequency audit.

    Reads from config['lexicon']['archive'] paths. Returns a flat list of
    dicts, each with at minimum: phrase, source_file, old_category.
    Does NOT populate shorthand lists — archive is audit-only, never matching.

    Returns empty list if no archive subsection exists.
    """
    lex_cfg = config.get("lexicon", {})
    archive_cfg = lex_cfg.get("archive", {})
    if not archive_cfg:
        logger.warning("No [lexicon.archive] section found in config")
        return []

    entries: List[Dict[str, Any]] = []

    # Map of archive path keys to source file names
    path_keys = [
        ("phrase_bank_path", "phrase_bank.jsonl"),
        ("chaos_lexicon_path", "chaos_lexicon.jsonl"),
        ("privacy_lexicon_path", "privacy_lexicon.jsonl"),
        ("drop_sentence_words_path", "drop_sentence_words.jsonl"),
        ("mask_words_path", "mask_words.jsonl"),
        ("phrase_candidates_path", "phrase_candidates.jsonl"),
        ("manual_keep_path", "manual_keep.jsonl"),
        ("manual_drop_path", "manual_drop.jsonl"),
    ]

    for config_key, source_name in path_keys:
        raw = archive_cfg.get(config_key, "")
        if not raw:
            continue
        path = _resolve_path(raw, ai_root)
        if not path.exists():
            logger.warning("Archive file not found: %s", path)
            continue

        file_entries = _load_jsonl(path)
        for entry in file_entries:
            entry.setdefault("source_file", source_name)
            entry.setdefault("old_category", "unknown")
            entry.setdefault("participates_in_matching", False)
        entries.extend(file_entries)
        logger.info("Archive loaded: %d entries from %s", len(file_entries), source_name)

    # Also load stoplist archive
    raw_stop = archive_cfg.get("phrase_stoplist_path", "")
    if raw_stop:
        path_stop = _resolve_path(raw_stop, ai_root)
        if path_stop.exists():
            stoplist = _load_stoplist(path_stop)
            for word in sorted(stoplist):
                entries.append({
                    "phrase": word,
                    "source_file": "phrase_stoplist.txt",
                    "old_category": "phrase_mining.stoplist",
                    "participates_in_matching": False,
                })
            logger.info("Archive stoplist: %d entries", len(stoplist))

    logger.info("Total archive terms loaded: %d", len(entries))
    return entries


# ---------------------------------------------------------------------------
# Convenience: inject lexicon into config for downstream use
# ---------------------------------------------------------------------------
def inject_lexicon(config: Dict[str, Any], ai_root: Path) -> Dict[str, Any]:
    """Load lexicons and inject them into config['_lexicon'].

    Downstream code (scorer, style_tagger) reads config['_lexicon'] instead
    of directly indexing config['style_markers'] / config['chaos'] / etc.
    The original inline lists in TOML are preserved (but deprecated).

    v0.3: Also loads archive terms into config['_archive_terms'] for the
    frequency audit stage. Archive terms never contribute to active matching
    shorthand lists.
    """
    lex = load_lexicons(config, ai_root)
    config["_lexicon"] = lex

    # Load archive terms (read-only, audit only)
    archive_terms = load_archive_terms(config, ai_root)
    config["_archive_terms"] = archive_terms
    if archive_terms:
        logger.info("Archive terms loaded: %d total (audit-only, not for matching)",
                     len(archive_terms))

    return config
