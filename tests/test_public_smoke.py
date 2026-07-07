"""Public CI smoke tests that do not depend on private QQ exports."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from qq_raw_filter.block_builder import extract_my_blocks, messages_to_turns, turns_to_sessions
from qq_raw_filter.bucket import classify_block
from qq_raw_filter.config_loader import default_config
from qq_raw_filter.qce_parser import is_self, parse_qce_json
from qq_raw_filter.scorer import score_chaos, score_junk, score_privacy, score_style


ROOT = Path(__file__).resolve().parent.parent


def _sample_qce_payload() -> dict:
    return {
        "chatInfo": {
            "selfUid": "self-uid",
            "selfUin": "10001",
            "selfName": "Me",
            "name": "Synthetic chat",
            "type": "private",
        },
        "messages": [
            {
                "id": "1",
                "seq": "1",
                "timestamp": 1710000000000,
                "sender": {"uid": "self-uid", "uin": "10001", "name": "Me"},
                "type": "type_1",
                "content": {
                    "text": (
                        "This synthetic paragraph is long enough to become a style "
                        "candidate. It has structure, reflection, and a few turns of "
                        "thought so the scoring pipeline can exercise normal paths."
                    )
                },
            },
            {
                "id": "2",
                "seq": "2",
                "timestamp": 1710000010000,
                "sender": {"uid": "self-uid", "uin": "10001", "name": "Me"},
                "type": "type_1",
                "content": {
                    "text": (
                        "A second synthetic self message keeps the block above the "
                        "minimum message threshold while still avoiding any private "
                        "or real chat material."
                    )
                },
            },
            {
                "id": "3",
                "seq": "3",
                "timestamp": 1710000030000,
                "sender": {"uid": "other-uid", "uin": "20002", "name": "Other"},
                "type": "type_1",
                "content": {"text": "Thanks, that gives the parser another speaker."},
            },
        ],
    }


def test_parse_and_score_synthetic_qce(tmp_path: Path) -> None:
    sample = tmp_path / "sample.json"
    sample.write_text(json.dumps(_sample_qce_payload(), ensure_ascii=False), encoding="utf-8")

    messages, info, warnings = parse_qce_json(sample)

    assert warnings == []
    assert info is not None
    assert info.self_uid == "self-uid"
    assert len(messages) == 3
    assert is_self(messages[0], ["self-uid"], ["Me"])

    cfg = default_config()
    cfg["identity"]["me_ids"] = ["self-uid"]
    cfg["identity"]["me_names"] = ["Me"]

    turns, turn_warnings = messages_to_turns(messages, cfg["identity"]["me_ids"], cfg["identity"]["me_names"])
    sessions = turns_to_sessions(turns, str(sample))
    blocks, block_warnings = extract_my_blocks(sessions, cfg)

    assert turn_warnings == []
    assert block_warnings == []
    assert blocks

    block = blocks[0]
    assert 0 <= score_style(block, cfg) <= 15
    assert 0 <= score_privacy(block, cfg) <= 15
    assert 0 <= score_junk(block, cfg) <= 10
    assert 0 <= score_chaos(block, cfg) <= 10

    bucket, reasons = classify_block(block, cfg)
    assert bucket in {"candidates", "micro_style", "need_anonymize", "chaos_style", "debatable", "rejected"}
    assert reasons


def test_cli_help_entrypoints() -> None:
    commands = [
        [sys.executable, str(ROOT / "qce_block_filter.py"), "--help"],
        [sys.executable, "-m", "qq_raw_filter._cli", "--help"],
    ]
    for command in commands:
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, result.stderr
        assert "QQ Chat Exporter raw material filter" in result.stdout
