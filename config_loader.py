"""
config_loader.py — Load and validate filter_config.toml for QQ raw material filter.

Provides:
- load_config(path) -> FilterConfig
- default_config() -> dict (fallback if file missing)
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

    # --- style_markers / chaos lists ---
    for list_key in ("style_markers", "chaos", "privacy", "light_interruption"):
        section = cfg.get(list_key, {})
        for sub_key in section:
            if isinstance(section[sub_key], list):
                for item in section[sub_key]:
                    if not isinstance(item, str):
                        errors.append(f"{list_key}.{sub_key}: expected list of strings")

    if errors:
        raise ConfigValidationError("\n".join(errors))
    return cfg


def default_config() -> Dict[str, Any]:
    """Return a complete default configuration dict."""
    return {
        "meta": {
            "config_version": "0.1",
            "description": "QQ character skill raw material filter config",
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
        },
        "ratio": {
            "max_short_fragment_ratio": 0.70,
            "max_chaos_ratio_for_candidate": 0.30,
            "max_other_char_ratio_inside_my_block": 0.45,
        },
        "dedup": {
            "duplicate_keep_limit": 3,
            "near_duplicate_enable": False,
        },
        "output": {
            "keep_rejected": True,
            "keep_original_fragments": True,
            "write_debug_sessions": True,
            "write_active_config_snapshot": True,
            "write_human_review_samples": True,
            "human_review_sample_size": 100,
        },
        "style_markers": {
            "analysis": [
                "我感觉", "其实", "本质上", "主要是", "关键是", "问题是",
                "不是", "而是", "先别", "没必要", "倒也", "说白了",
                "这么说", "怎么说呢",
            ],
            "tone": [
                "有点抽象", "绷不住", "确实", "离谱", "草", "啊？",
            ],
        },
        "light_interruption": {
            "phrases": [
                "啊？", "为啥", "然后呢", "确实", "？", "细说", "哈哈哈", "什么意思",
            ],
        },
        "privacy": {
            "high_risk_words": [
                "密码", "验证码", "身份证", "银行卡", "token", "api_key", "secret",
            ],
            "medium_risk_words": [
                "学校", "学院", "班级", "宿舍", "寝室", "老师", "手机号",
                "电话", "地址", "学号",
            ],
        },
        "chaos": {
            "mild_words": ["草", "绷不住", "抽象", "离谱"],
            "strong_words": ["傻逼", "脑残", "滚", "死"],
        },
    }


def resolve_paths(cfg: Dict[str, Any], ai_root: Path) -> Dict[str, Any]:
    """Resolve relative paths in config to absolute paths rooted at ai_root.

    Modifies cfg['path'] in-place and returns cfg.
    """
    if "path" not in cfg:
        return cfg
    for key in ("input_dir", "output_dir"):
        raw = cfg["path"].get(key, "")
        if raw:
            p = Path(raw)
            if not p.is_absolute():
                cfg["path"][key] = str(ai_root / p)
            else:
                cfg["path"][key] = str(p)
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
    print("Default config loaded OK")
    print(f"  session_gap_minutes = {cfg['time']['session_gap_minutes']}")
    print(f"  style_markers.analysis = {len(cfg['style_markers']['analysis'])} entries")
    print(f"  identity.me_ids = {cfg['identity']['me_ids']}")
