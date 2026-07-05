"""
config_loader.py — Load and validate filter_config.toml for QQ raw material filter.

Provides:
- load_config(path) -> FilterConfig
- default_config() -> dict (fallback if file missing)
- resolve_paths(cfg, ai_root) -> cfg with absolute paths
- resolve_lexicon_paths(cfg, ai_root) -> cfg with absolute lexicon paths
- ConfigValidationError
"""

from __future__ import annotations

import sys
import copy
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]


class ConfigValidationError(Exception):
    """Raised when configuration values are invalid."""


def _check_range(val: Any, name: str, lo: float, hi: float) -> None:
    if not isinstance(val, (int, float)):
        raise ConfigValidationError(f"{name}: expected number, got {type(val).__name__}")
    if val < lo or val > hi:
        raise ConfigValidationError(f"{name}: {val} is outside [{lo}, {hi}]")


def _check_positive(val: Any, name: str) -> None:
    if not isinstance(val, (int, float)):
        raise ConfigValidationError(f"{name}: expected number, got {type(val).__name__}")
    if val <= 0:
        raise ConfigValidationError(f"{name}: must be positive, got {val}")


def _check_non_negative(val: Any, name: str) -> None:
    if not isinstance(val, (int, float)):
        raise ConfigValidationError(f"{name}: expected number, got {type(val).__name__}")
    if val < 0:
        raise ConfigValidationError(f"{name}: must be non-negative, got {val}")


def _check_int(val: Any, name: str) -> None:
    if not isinstance(val, int):
        raise ConfigValidationError(f"{name}: expected int, got {type(val).__name__}")


def _validate_lexicon_subsection(
    section: Dict[str, Any], prefix: str, errors: List[str]
) -> None:
    """Validate a lexicon sub-section (archive, active, candidates)."""
    valid_path_keys = {
        "phrase_bank_path", "phrase_candidates_path", "phrase_stoplist_path",
        "privacy_lexicon_path", "chaos_lexicon_path",
        "drop_sentence_words_path", "mask_words_path",
        "manual_keep_path", "manual_drop_path",
    }
    for k, v in section.items():
        if k not in valid_path_keys:
            errors.append(f"{prefix}.{k}: unknown key")
        elif not isinstance(v, str):
            errors.append(f"{prefix}.{k}: expected string path")


def _validate_audit_subsection(
    section: Dict[str, Any], errors: List[str]
) -> None:
    """Validate [lexicon.audit] subsection."""
    for k, v in section.items():
        if k in ("enabled", "output_audit_file", "output_audit_report"):
            if not isinstance(v, bool):
                errors.append(f"lexicon.audit.{k}: expected bool")
        else:
            errors.append(f"lexicon.audit.{k}: unknown key")


def validate_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Validate configuration dict in-place, returning it for chaining."""
    errors: List[str] = []

    # --- identity ---
    if "identity" in cfg:
        ident = cfg["identity"]
        for k in ("me_ids", "me_names"):
            if k in ident and not isinstance(ident[k], list):
                errors.append(f"identity.{k}: expected list")
            if k in ident and not ident[k]:
                pass  # empty is valid (will be configured later)

    # --- time ---
    if "time" in cfg:
        t = cfg["time"]
        _check_positive(t.get("session_gap_minutes", 20), "time.session_gap_minutes")
        _check_positive(t.get("turn_gap_seconds", 90), "time.turn_gap_seconds")
        _check_positive(t.get("my_block_continue_seconds", 180), "time.my_block_continue_seconds")

    # --- interruption ---
    if "interruption" in cfg:
        i = cfg["interruption"]
        _check_non_negative(i.get("light_interruption_chars", 30), "interruption.light_interruption_chars")
        _check_non_negative(i.get("hard_interruption_chars", 100), "interruption.hard_interruption_chars")
        _check_non_negative(i.get("max_light_interruptions", 5), "interruption.max_light_interruptions")
        if i.get("light_interruption_chars", 30) > i.get("hard_interruption_chars", 100):
            errors.append("interruption.light_interruption_chars must be <= hard_interruption_chars")

    # --- my_block ---
    if "my_block" in cfg:
        mb = cfg["my_block"]
        _check_non_negative(mb.get("min_my_block_chars", 50), "my_block.min_my_block_chars")
        _check_non_negative(mb.get("good_my_block_chars", 120), "my_block.good_my_block_chars")
        _check_range(mb.get("min_my_char_ratio", 0.50), "my_block.min_my_char_ratio", 0, 1)
        _check_int(mb.get("min_my_msg_count", 2), "my_block.min_my_msg_count")
        _check_positive(mb.get("short_fragment_chars", 3), "my_block.short_fragment_chars")
        _check_positive(mb.get("max_block_chars", 1200), "my_block.max_block_chars")
        _check_positive(mb.get("max_fragment_count", 120), "my_block.max_fragment_count")

    # --- message ---
    if "message" in cfg:
        msg = cfg["message"]
        _check_non_negative(msg.get("min_isolated_message_chars", 6), "message.min_isolated_message_chars")
        _check_non_negative(msg.get("very_short_chars", 3), "message.very_short_chars")
        _check_positive(msg.get("max_single_message_chars", 500), "message.max_single_message_chars")

    # --- score ---
    if "score" in cfg:
        sc = cfg["score"]
        _check_non_negative(sc.get("candidate_min_style_score", 5), "score.candidate_min_style_score")
        _check_non_negative(sc.get("candidate_min_chars_when_style_high", 30), "score.candidate_min_chars_when_style_high")
        _check_non_negative(sc.get("micro_style_min_style_score", 2), "score.micro_style_min_style_score")
        _check_non_negative(sc.get("need_anonymize_min_style_score", 3), "score.need_anonymize_min_style_score")
        _check_non_negative(sc.get("junk_reject_score", 6), "score.junk_reject_score")
        _check_non_negative(sc.get("chaos_separate_score", 4), "score.chaos_separate_score")
        _check_non_negative(sc.get("debatable_min_style_score", 4), "score.debatable_min_style_score")
        _check_non_negative(sc.get("debatable_max_per_run", 200), "score.debatable_max_per_run")
        _check_non_negative(sc.get("privacy_medium_threshold", 5), "score.privacy_medium_threshold")

    # --- ratio ---
    if "ratio" in cfg:
        r = cfg["ratio"]
        _check_range(r.get("max_short_fragment_ratio", 0.70), "ratio.max_short_fragment_ratio", 0, 1)
        _check_range(r.get("max_chaos_ratio_for_candidate", 0.30), "ratio.max_chaos_ratio_for_candidate", 0, 1)
        _check_range(r.get("max_other_char_ratio_inside_my_block", 0.45), "ratio.max_other_char_ratio_inside_my_block", 0, 1)

    # --- dedup ---
    if "dedup" in cfg:
        dd = cfg["dedup"]
        if not isinstance(dd.get("duplicate_keep_limit", 3), int):
            errors.append("dedup.duplicate_keep_limit: expected int")

    # --- output ---
    if "output" in cfg:
        out = cfg["output"]
        for k in ("keep_rejected", "keep_original_fragments", "write_debug_sessions",
                   "write_active_config_snapshot", "write_human_review_samples"):
            if k in out and not isinstance(out[k], bool):
                errors.append(f"output.{k}: expected bool")

    # --- filter ---
    if "filter" in cfg:
        flt = cfg["filter"]
        for k in ("drop_sentence_words", "mask_words", "private_names", "private_places"):
            if k in flt and not isinstance(flt[k], list):
                errors.append(f"filter.{k}: expected list")
            if k in flt:
                for item in flt[k]:
                    if not isinstance(item, str):
                        errors.append(f"filter.{k}: expected list of strings")

    # --- privacy mode ---
    if "privacy" in cfg:
        p = cfg["privacy"]
        mode = p.get("mode", "balanced")
        if mode not in ("strict", "balanced", "recall"):
            errors.append(f"privacy.mode: expected 'strict', 'balanced', or 'recall', got '{mode}'")
        # high/medium_risk_words are now loaded from lexicon; inline lists
        # are deprecated but still validated if present
        for k in ("high_risk_words", "medium_risk_words"):
            if k in p and not isinstance(p[k], list):
                errors.append(f"privacy.{k}: expected list")

    # --- debatable ---
    if "debatable" in cfg:
        d = cfg["debatable"]
        if "enable" in d and not isinstance(d["enable"], bool):
            errors.append("debatable.enable: expected bool")
        if "max_per_run" in d and not isinstance(d["max_per_run"], int):
            errors.append("debatable.max_per_run: expected int")

    # --- style_tags ---
    if "style_tags" in cfg:
        st = cfg["style_tags"]
        if "enable" in st and not isinstance(st["enable"], bool):
            errors.append("style_tags.enable: expected bool")
        # argument/analysis markers now loaded from lexicon; inline lists
        # are deprecated but validated if present
        for k in ("argument_markers", "analysis_markers"):
            if k in st and not isinstance(st[k], list):
                errors.append(f"style_tags.{k}: expected list")

    # --- lexicon (v0.3: archive + audit subsections added) ---
    if "lexicon" in cfg:
        lex = cfg["lexicon"]
        valid_keys = {
            "phrase_bank_path", "phrase_candidates_path", "phrase_stoplist_path",
            "privacy_lexicon_path", "chaos_lexicon_path",
            "drop_sentence_words_path", "mask_words_path",
            "manual_keep_path", "manual_drop_path",
            "archive", "audit",
        }
        for k in lex:
            if k not in valid_keys:
                if k not in ("archive", "audit"):
                    errors.append(f"lexicon.{k}: unknown key")
            elif k == "archive":
                _validate_lexicon_subsection(lex["archive"], "lexicon.archive", errors)
            elif k == "audit":
                _validate_audit_subsection(lex["audit"], errors)
            elif not isinstance(lex[k], str):
                errors.append(f"lexicon.{k}: expected string path")

    # --- phrase_mining (new in v0.2) ---
    if "phrase_mining" in cfg:
        pm = cfg["phrase_mining"]
        if "enabled" in pm and not isinstance(pm["enabled"], bool):
            errors.append("phrase_mining.enabled: expected bool")
        for k in ("min_n", "max_n", "min_freq", "top_k_each_length"):
            if k in pm and not isinstance(pm[k], int):
                errors.append(f"phrase_mining.{k}: expected int")
        for k in ("min_pmi_2gram", "min_pmi_3gram", "min_pmi_4gram", "min_entropy"):
            if k in pm and not isinstance(pm[k], (int, float)):
                errors.append(f"phrase_mining.{k}: expected number")
        if "scan_sources" in pm and not isinstance(pm["scan_sources"], list):
            errors.append("phrase_mining.scan_sources: expected list")
        if "bucket_weight" in pm:
            bw = pm["bucket_weight"]
            for bk in ("candidates", "micro_style", "chaos_style", "need_anonymize", "rejected"):
                if bk in bw and not isinstance(bw[bk], (int, float)):
                    errors.append(f"phrase_mining.bucket_weight.{bk}: expected number")
        if "output" in pm:
            po = pm["output"]
            for k in ("write_phrase_freq_by_length", "write_phrase_candidates",
                       "write_phrase_report", "update_auto_phrase_candidates",
                       "update_phrase_bank"):
                if k in po and not isinstance(po[k], bool):
                    errors.append(f"phrase_mining.output.{k}: expected bool")

    # --- tuning_advice ---
    if "tuning_advice" in cfg:
        ta = cfg["tuning_advice"]
        if "enable" in ta and not isinstance(ta["enable"], bool):
            errors.append("tuning_advice.enable: expected bool")
        for k in ("candidate_ratio_target_min", "candidate_ratio_target_max", "chaos_style_ratio_max"):
            if k in ta and not isinstance(ta[k], (int, float)):
                errors.append(f"tuning_advice.{k}: expected number")

    if errors:
        raise ConfigValidationError("\n".join(errors))
    return cfg


def default_config() -> Dict[str, Any]:
    """Return a complete default configuration dict."""
    return {
        "meta": {
            "config_version": "0.3",
            "description": "QQ character skill raw material filter config — lexicon registry mode",
        },
        "identity": {"me_ids": [], "me_names": []},
        "path": {
            "input_dir": "raw_material/qq/exports/raw/qq-chat-exporter-live",
            "output_dir": "raw_material/qq/exports/normalized",
        },
        "time": {
            "session_gap_minutes": 20,
            "turn_gap_seconds": 90,
            "my_block_continue_seconds": 180,
        },
        "interruption": {
            "light_interruption_chars": 30,
            "hard_interruption_chars": 100,
            "max_light_interruptions": 5,
        },
        "my_block": {
            "min_my_block_chars": 50,
            "good_my_block_chars": 120,
            "min_my_char_ratio": 0.50,
            "min_my_msg_count": 2,
            "short_fragment_chars": 3,
            "max_block_chars": 1200,
            "max_fragment_count": 120,
        },
        "message": {
            "min_isolated_message_chars": 6,
            "very_short_chars": 3,
            "max_single_message_chars": 500,
        },
        "score": {
            "candidate_min_style_score": 5,
            "candidate_min_chars_when_style_high": 30,
            "micro_style_min_style_score": 2,
            "need_anonymize_min_style_score": 3,
            "junk_reject_score": 6,
            "chaos_separate_score": 4,
            "debatable_min_style_score": 4,
            "debatable_max_per_run": 200,
            "privacy_medium_threshold": 5,
        },
        "ratio": {
            "max_short_fragment_ratio": 0.70,
            "max_chaos_ratio_for_candidate": 0.30,
            "max_other_char_ratio_inside_my_block": 0.45,
        },
        "dedup": {
            "duplicate_keep_limit": 3,
            "near_duplicate_enable": False,
            "near_duplicate_threshold": 0.85,
        },
        "output": {
            "keep_rejected": True,
            "keep_original_fragments": True,
            "write_debug_sessions": True,
            "write_active_config_snapshot": True,
            "write_human_review_samples": True,
            "human_review_sample_size": 100,
        },
        "filter": {
            "drop_sentence_words": [],
            "mask_words": [],
            "private_names": [],
            "private_places": [],
        },
        "privacy": {
            "mode": "balanced",
        },
        "debatable": {
            "enable": True,
            "max_per_run": 200,
        },
        "style_tags": {
            "enable": True,
            "argument_markers": [],
            "analysis_markers": [],
        },
        "tuning_advice": {
            "enable": True,
            "candidate_ratio_target_min": 0.05,
            "candidate_ratio_target_max": 0.12,
            "chaos_style_ratio_max": 0.10,
        },
        "lexicon": {
            # Active lexicon paths (v0.3: used in matching)
            "phrase_bank_path": "workspace/scripts/raw_material_filter/lexicons/phrase_bank.jsonl",
            "phrase_candidates_path": "workspace/scripts/raw_material_filter/lexicons/phrase_candidates.jsonl",
            "phrase_stoplist_path": "workspace/scripts/raw_material_filter/lexicons/phrase_stoplist.txt",
            "privacy_lexicon_path": "workspace/scripts/raw_material_filter/lexicons/privacy_lexicon.jsonl",
            "chaos_lexicon_path": "workspace/scripts/raw_material_filter/lexicons/chaos_lexicon.jsonl",
            "drop_sentence_words_path": "workspace/scripts/raw_material_filter/lexicons/drop_sentence_words.jsonl",
            "mask_words_path": "workspace/scripts/raw_material_filter/lexicons/mask_words.jsonl",
            "manual_keep_path": "workspace/scripts/raw_material_filter/lexicons/manual_keep.jsonl",
            "manual_drop_path": "workspace/scripts/raw_material_filter/lexicons/manual_drop.jsonl",
            # Archive paths (v0.3: read-only, audit only, never used for matching)
            "archive": {
                "phrase_bank_path": "workspace/scripts/raw_material_filter/lexicons/archive/phrase_bank_archive.jsonl",
                "phrase_candidates_path": "workspace/scripts/raw_material_filter/lexicons/archive/phrase_candidates_archive.jsonl",
                "phrase_stoplist_path": "workspace/scripts/raw_material_filter/lexicons/archive/phrase_stoplist_archive.txt",
                "privacy_lexicon_path": "workspace/scripts/raw_material_filter/lexicons/archive/privacy_lexicon_archive.jsonl",
                "chaos_lexicon_path": "workspace/scripts/raw_material_filter/lexicons/archive/chaos_lexicon_archive.jsonl",
                "drop_sentence_words_path": "workspace/scripts/raw_material_filter/lexicons/archive/drop_sentence_words_archive.jsonl",
                "mask_words_path": "workspace/scripts/raw_material_filter/lexicons/archive/mask_words_archive.jsonl",
                "manual_keep_path": "workspace/scripts/raw_material_filter/lexicons/archive/manual_keep_archive.jsonl",
                "manual_drop_path": "workspace/scripts/raw_material_filter/lexicons/archive/manual_drop_archive.jsonl",
            },
            # Audit configuration (v0.3)
            "audit": {
                "enabled": True,
                "output_audit_file": True,
                "output_audit_report": True,
            },
        },
        "phrase_mining": {
            "enabled": True,
            "min_n": 2,
            "max_n": 4,
            "min_freq": 5,
            "min_pmi_2gram": 2.0,
            "min_pmi_3gram": 3.0,
            "min_pmi_4gram": 4.0,
            "min_entropy": 0.5,
            "top_k_each_length": 500,
            "scan_sources": ["my_text", "fragments", "my_blocks"],
            "bucket_weight": {
                "candidates": 1.0,
                "micro_style": 1.2,
                "chaos_style": 0.5,
                "need_anonymize": 0.4,
                "rejected": -0.5,
            },
            "keyness": {
                "enabled": True,
                "compare_candidates_against_rejected": True,
                "min_keyness": 1.5,
            },
            "output": {
                "write_phrase_freq_by_length": True,
                "write_phrase_candidates": True,
                "write_phrase_report": True,
                "update_auto_phrase_candidates": True,
                "update_phrase_bank": False,
            },
        },
    }


def resolve_paths(cfg: Dict[str, Any], ai_root: Path) -> Dict[str, Any]:
    """Resolve relative paths in config to absolute paths rooted at ai_root.

    Handles:
      - path.input_dir, path.output_dir
      - lexicon.*_path
      - phrase_mining.* (no file paths)
    Modifies cfg in-place and returns cfg.
    """
    if "path" in cfg:
        for key in ("input_dir", "output_dir"):
            raw = cfg["path"].get(key, "")
            if raw:
                p = Path(raw)
                if not p.is_absolute():
                    cfg["path"][key] = str(ai_root / p)
                else:
                    cfg["path"][key] = str(p)

    # Resolve lexicon paths (both active and archive subsections)
    if "lexicon" in cfg:
        for key in list(cfg["lexicon"].keys()):
            val = cfg["lexicon"][key]
            if isinstance(val, dict):
                # Handle nested subsections (archive, audit)
                for sub_key in list(val.keys()):
                    raw = val[sub_key]
                    if raw and isinstance(raw, str):
                        p = Path(raw)
                        if not p.is_absolute():
                            val[sub_key] = str(ai_root / p)
                        else:
                            val[sub_key] = str(p)
            elif isinstance(val, str):
                # Handle flat keys (active paths and backward compat)
                p = Path(val)
                if not p.is_absolute():
                    cfg["lexicon"][key] = str(ai_root / p)
                else:
                    cfg["lexicon"][key] = str(val)

    return cfg


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load configuration from a TOML file, falling back to defaults."""
    cfg = default_config()
    if path and path.exists():
        raw = path.read_bytes()
        parsed = tomllib.loads(raw.decode("utf-8"))
        _deep_merge(cfg, parsed)
    validate_config(cfg)
    return cfg


def _deep_merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> None:
    """Recursively merge overlay into base (modifies base in-place)."""
    for key, value in overlay.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = copy.deepcopy(value)


def snap_config(cfg: Dict[str, Any]) -> str:
    """Serialize config to TOML string for active_config snapshot."""
    lines: List[str] = []
    _write_toml(lines, "", cfg)
    return "\n".join(lines)


def _escape_toml_str(s: str) -> str:
    """Escape backslashes and double-quotes for a TOML basic string value."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _write_toml(lines: List[str], prefix: str, obj: Any) -> None:
    """Helper: recursively write a dict as TOML."""
    if isinstance(obj, dict):
        if prefix:
            lines.append(f"[{prefix}]")
        for key, val in obj.items():
            full_key = f"{prefix}.{key}" if prefix else key
            if isinstance(val, dict):
                _write_toml(lines, full_key, val)
            elif isinstance(val, list):
                items = ", ".join(
                    f'"{_escape_toml_str(v)}"'
                    if isinstance(v, str)
                    else str(v).lower() if isinstance(v, bool)
                    else str(v)
                    for v in val
                )
                lines.append(f'{key} = [{items}]')
            elif isinstance(val, bool):
                lines.append(f"{key} = {str(val).lower()}")
            elif isinstance(val, str):
                lines.append(f'{key} = "{_escape_toml_str(val)}"')
            else:
                lines.append(f"{key} = {val}")
    else:
        lines.append(f"{prefix} = {obj}")


if __name__ == "__main__":
    # Quick self-test
    cfg = load_config()
    # Test lexicon path resolution
    from pathlib import Path
    ai_root = Path("D:/AI")
    cfg = resolve_paths(cfg, ai_root)
    print("Config loaded OK (v{})".format(cfg.get("meta", {}).get("config_version", "?")))
    print(f"  lexicon paths: {list(cfg.get('lexicon', {}).keys())}")
    print(f"  phrase_mining enabled: {cfg.get('phrase_mining', {}).get('enabled')}")
