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
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

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
    with path.open("r", encoding="utf-8") as f:
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


def _load_stoplist(path: Path) -> Set[str]:
    """Load a stoplist TXT file; one phrase per line."""
    if not path.exists():
        logger.warning("Stoplist not found, using empty: %s", path)
        return set()
    words: Set[str] = set()
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            w = line.strip()
            if w:
                words.add(w)
    logger.info("Loaded %d stopwords from %s", len(words), path)
    return words


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
    result["phrase_stoplist"] = _load_stoplist(
        _resolve_path(lex_cfg.get("phrase_stoplist_path", ""), ai_root)
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
# Convenience: inject lexicon into config for downstream use
# ---------------------------------------------------------------------------
def inject_lexicon(config: Dict[str, Any], ai_root: Path) -> Dict[str, Any]:
    """Load lexicons and inject them into config['_lexicon'].

    Downstream code (scorer, style_tagger) reads config['_lexicon'] instead
    of directly indexing config['style_markers'] / config['chaos'] / etc.
    The original inline lists in TOML are preserved (but deprecated).
    """
    lex = load_lexicons(config, ai_root)
    config["_lexicon"] = lex
    return config
