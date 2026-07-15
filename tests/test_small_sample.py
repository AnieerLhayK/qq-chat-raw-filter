"""
test_small_sample.py — Lightweight test of the QQ raw material filter pipeline.

Run:
  pytest tests/test_small_sample.py -v
  python tests/test_small_sample.py   (standalone)
"""

from __future__ import annotations

import json
import sys
import os
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qq_raw_filter.config_loader import load_config, validate_config, default_config
from qq_raw_filter.qce_parser import parse_qce_json, scan_input_dir, is_self, ParsedMessage
from qq_raw_filter.block_builder import messages_to_turns, turns_to_sessions, extract_my_blocks
from qq_raw_filter.scorer import score_style, score_privacy, score_junk, score_chaos
from qq_raw_filter.bucket import classify_block


SAMPLE_DATA_DIR = Path("${WORKSPACE_ROOT}/raw_material/qq/exports/character.zf/raw/qq-chat-exporter-live")


def _require_sample_data():
    if SAMPLE_DATA_DIR.exists() and any(SAMPLE_DATA_DIR.rglob("*.json")):
        return
    raise unittest.SkipTest(f"live QCE sample data not available: {SAMPLE_DATA_DIR}")


def _get_config():
    cfg = default_config()
    cfg["identity"]["me_ids"] = ["u_AEyXCg7MNhIZV9qhDMI10w", "1733930883"]
    return cfg


def test_config_loader_defaults():
    cfg = load_config(None)
    assert cfg["meta"]["config_version"] == "0.3"
    assert cfg["time"]["session_gap_minutes"] == 20
    assert cfg["my_block"]["min_my_block_chars"] == 50
    print("  OK config_loader defaults OK")


def test_config_validation():
    import pytest
    bad = _get_config()
    bad["my_block"]["min_my_char_ratio"] = 1.5
    with pytest.raises(Exception) as exc_info:
        validate_config(bad)
    assert "min_my_char_ratio" in str(exc_info.value)
    print("  OK config_validation rejects bad ratio")


def test_parse_qce_file():
    _require_sample_data()
    files = scan_input_dir(SAMPLE_DATA_DIR, limit_files=1)
    assert len(files) >= 1
    msgs, info, warns = parse_qce_json(files[0])
    assert len(msgs) > 0
    assert info is not None
    assert info.self_uid
    first = msgs[0]
    assert hasattr(first, "sender_uid")
    assert hasattr(first, "sender_name")
    assert isinstance(first.text, str)
    print(f"  OK parse_qce_file: {len(msgs)} msgs from {files[0].name}")


def test_is_self():
    _require_sample_data()
    cfg = _get_config()
    files = scan_input_dir(SAMPLE_DATA_DIR, limit_files=1)
    msgs, _, _ = parse_qce_json(files[0])
    own = [m for m in msgs if is_self(m, cfg["identity"]["me_ids"], cfg["identity"]["me_names"])]
    other = [m for m in msgs if not is_self(m, cfg["identity"]["me_ids"], cfg["identity"]["me_names"])]
    assert len(own) > 0
    assert len(other) > 0
    print(f"  OK is_self: {len(own)} own, {len(other)} other")


def test_messages_to_turns():
    _require_sample_data()
    cfg = _get_config()
    files = scan_input_dir(SAMPLE_DATA_DIR, limit_files=1)
    msgs, _, _ = parse_qce_json(files[0])
    turns, warns = messages_to_turns(msgs, cfg["identity"]["me_ids"], cfg["identity"]["me_names"])
    assert len(turns) > 0
    assert len(turns) <= len(msgs)
    assert hasattr(turns[0], "speaker_type")
    print(f"  OK messages_to_turns: {len(turns)} turns from {len(msgs)} msgs")


def test_turns_to_sessions():
    _require_sample_data()
    cfg = _get_config()
    files = scan_input_dir(SAMPLE_DATA_DIR, limit_files=1)
    msgs, info, _ = parse_qce_json(files[0])
    turns, _ = messages_to_turns(msgs, cfg["identity"]["me_ids"], cfg["identity"]["me_names"])
    sessions = turns_to_sessions(turns, str(files[0]))
    assert len(sessions) >= 1
    total = sum(len(s.turns) for s in sessions)
    assert total == len(turns)
    print(f"  OK turns_to_sessions: {len(sessions)} sessions")


def test_extract_my_blocks():
    _require_sample_data()
    cfg = _get_config()
    files = scan_input_dir(SAMPLE_DATA_DIR, limit_files=1)
    msgs, _, _ = parse_qce_json(files[0])
    turns, _ = messages_to_turns(msgs, cfg["identity"]["me_ids"], cfg["identity"]["me_names"])
    sessions = turns_to_sessions(turns, str(files[0]))
    blocks, warns = extract_my_blocks(sessions, cfg)
    print(f"  OK extract_my_blocks: {len(blocks)} blocks, warns={len(warns)}")
    if blocks:
        b = blocks[0]
        assert b.metrics["my_char_count"] > 0
        print(f"    First: {b.block_id}, my_chars={b.metrics['my_char_count']}")


def test_scoring():
    _require_sample_data()
    cfg = _get_config()
    files = scan_input_dir(SAMPLE_DATA_DIR, limit_files=1)
    msgs, _, _ = parse_qce_json(files[0])
    turns, _ = messages_to_turns(msgs, cfg["identity"]["me_ids"], cfg["identity"]["me_names"])
    sessions = turns_to_sessions(turns, str(files[0]))
    blocks, _ = extract_my_blocks(sessions, cfg)
    if not blocks:
        print("  WARN: No blocks to score")
        return
    b = blocks[0]
    style = score_style(b, cfg)
    privacy = score_privacy(b, cfg)
    junk = score_junk(b, cfg)
    chaos = score_chaos(b, cfg)
    assert 0 <= style <= 15
    assert 0 <= privacy <= 15
    assert 0 <= junk <= 10
    assert 0 <= chaos <= 10
    print(f"  OK scoring: style={style}, privacy={privacy}, junk={junk}, chaos={chaos}")


def test_bucket_classify():
    _require_sample_data()
    cfg = _get_config()
    files = scan_input_dir(SAMPLE_DATA_DIR, limit_files=1)
    msgs, _, _ = parse_qce_json(files[0])
    turns, _ = messages_to_turns(msgs, cfg["identity"]["me_ids"], cfg["identity"]["me_names"])
    sessions = turns_to_sessions(turns, str(files[0]))
    blocks, _ = extract_my_blocks(sessions, cfg)
    if not blocks:
        print("  WARN: No blocks to classify")
        return
    b = blocks[0]
    bucket, reasons = classify_block(b, cfg)
    valid = {"candidates", "micro_style", "need_anonymize", "chaos_style", "rejected"}
    assert bucket in valid
    assert len(reasons) > 0
    print(f"  OK classify: {bucket}, reasons={reasons}")


def test_live_quick_run():
    _require_sample_data()
    cfg = _get_config()
    files = scan_input_dir(SAMPLE_DATA_DIR, limit_files=3)
    all_msgs = []
    for f in files:
        msgs, _, _ = parse_qce_json(f)
        all_msgs.extend(msgs)
    assert len(all_msgs) > 0
    print(f"  OK live_quick_run: {len(all_msgs)} msgs from {len(files)} files")


def run_all():
    tests = [
        test_config_loader_defaults,
        test_config_validation,
        test_parse_qce_file,
        test_is_self,
        test_messages_to_turns,
        test_turns_to_sessions,
        test_extract_my_blocks,
        test_scoring,
        test_bucket_classify,
        test_live_quick_run,
    ]
    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except unittest.SkipTest as e:
            print(f"  SKIP {test.__name__}: {e}")
        except Exception as e:
            print(f"  FAIL {test.__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed, {len(tests)} total")
    return failed == 0


if __name__ == "__main__":
    success = run_all()
    sys.exit(0 if success else 1)
