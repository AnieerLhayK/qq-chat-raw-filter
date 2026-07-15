"""Synthetic regression tests for corpus-quality controls."""

from __future__ import annotations

import json
from pathlib import Path

from qq_raw_filter.block_builder import MyBlock
from qq_raw_filter.config_loader import default_config, snap_config
from qq_raw_filter.dedup import dedup_blocks
from qq_raw_filter.phrase_cluster import cluster_phrase_candidates
from qq_raw_filter.phrase_miner import mine_phrases
from qq_raw_filter.pipeline import (
    stage_extract_my_blocks,
    stage_merge_turns,
    stage_parse_files,
    stage_split_sessions,
)
from qq_raw_filter.review_feedback import (
    apply_block_review_decisions,
    attach_review_ids,
    load_review_decisions,
)


def _qce_payload(sender: str, timestamp: int, text: str) -> dict:
    return {
        "chatInfo": {"selfUid": "me", "selfName": "Me", "name": sender, "type": "private"},
        "messages": [{
            "id": "1", "seq": "1", "timestamp": timestamp,
            "sender": {"uid": "me", "name": "Me"}, "type": "type_1",
            "content": {"text": text},
        }],
    }


def _block(text: str) -> MyBlock:
    block = MyBlock(source_file="synthetic.json", session_id="s1")
    block.my_text = text
    block.metrics = {"my_char_count": len(text), "my_turn_count": 1}
    return block


def test_pipeline_keeps_chat_files_separate_with_interleaved_timestamps(tmp_path: Path) -> None:
    (tmp_path / "a.json").write_text(json.dumps(_qce_payload("A", 1000, "甲聊天内容")), encoding="utf-8")
    (tmp_path / "b.json").write_text(json.dumps(_qce_payload("B", 1001, "乙聊天内容")), encoding="utf-8")
    cfg = default_config()
    cfg["path"]["input_dir"] = str(tmp_path)
    cfg["identity"]["me_ids"] = ["me"]
    cfg["my_block"].update({"min_my_block_chars": 1, "min_my_msg_count": 1, "min_my_char_ratio": 0})
    ctx = {"config": cfg, "args": None}

    stage_parse_files(ctx)
    stage_merge_turns(ctx)
    stage_split_sessions(ctx)
    stage_extract_my_blocks(ctx)

    blocks = ctx["blocks"]
    assert len(blocks) == 2
    assert len({block.source_file for block in blocks}) == 2
    assert len({block.block_id for block in blocks}) == 2


def test_rejected_occurrences_lower_phrase_keyness_and_keep_it_out_of_candidates() -> None:
    candidate = _block("甲个人乙")
    rejected_one = _block("丙个人丁")
    rejected_two = _block("戊个人己")
    cfg = default_config()
    cfg["phrase_mining"].update({"min_n": 2, "max_n": 2, "min_freq": 2, "min_pmi_2gram": 0, "min_entropy": 0})
    cfg["phrase_mining"]["keyness"] = {"enabled": True, "compare_candidates_against_rejected": True, "min_keyness": 1.5}
    bucketed = {"candidates": [candidate], "micro_style": [], "chaos_style": [], "need_anonymize": [], "rejected": [rejected_one, rejected_two]}

    result = mine_phrases([candidate, rejected_one, rejected_two], bucketed, cfg, {"phrase_stoplist": set()})
    phrase = next(entry for entry in result["phrase_freq_2gram"] if entry["phrase"] == "个人")

    assert phrase["style_keyness"] < 1.5
    assert phrase["suggested_action"] == "skip"
    assert "个人" not in {entry["phrase"] for entry in result["phrase_candidates"]}


def test_review_decisions_override_only_when_the_block_is_not_already_filtered(tmp_path: Path) -> None:
    kept = _block("可复核的内容")
    filtered = _block("已经被过滤的内容")
    filtered.bucket = "rejected"
    attach_review_ids([kept, filtered])
    decisions_path = tmp_path / "review_decisions.jsonl"
    decisions_path.write_text("\n".join([
        json.dumps({"kind": "block", "key": kept.metrics["review_id"], "decision": "keep"}),
        json.dumps({"kind": "block", "key": filtered.metrics["review_id"], "decision": "keep"}),
    ]), encoding="utf-8")

    counts = apply_block_review_decisions([kept, filtered], load_review_decisions(decisions_path))

    assert kept.metrics["manual_review_decision"] == "keep"
    assert filtered.bucket == "rejected"
    assert counts == {"manual_keep_requested": 1, "manual_drop_applied": 0, "blocked_by_filter": 1}


def test_phrase_clusters_are_review_only_and_do_not_merge_unrelated_terms() -> None:
    clusters = cluster_phrase_candidates([
        {"phrase": "就是说", "freq": 8, "style_keyness": 3.0},
        {"phrase": "就是", "freq": 7, "style_keyness": 2.5},
        {"phrase": "完全不同", "freq": 9, "style_keyness": 4.0},
    ])

    assert len(clusters) == 1
    assert {member["phrase"] for member in clusters[0]["members"]} == {"就是说", "就是"}
    assert clusters[0]["review_status"] == "pending"


def test_near_duplicate_config_marks_similarity_without_changing_default_mode() -> None:
    first = _block("这是一个足够长的相近表达")
    second = _block("这是一个足够长的相近表达呀")
    removed, _ = dedup_blocks([first, second], near_duplicate_enable=True, near_duplicate_threshold=0.7)

    assert removed == 1
    assert second.bucket == "rejected"
    assert any(reason.startswith("near_duplicate:") for reason in second.reasons)


def test_config_snapshot_excludes_runtime_lexicon_payloads() -> None:
    cfg = default_config()
    cfg["_lexicon"] = {"phrase_bank": [{"phrase": "private runtime entry"}]}
    cfg["_archive_terms"] = [{"term": "private archive entry"}]

    snapshot = snap_config(cfg)

    assert "[_lexicon]" not in snapshot
    assert "[_archive_terms]" not in snapshot
    assert "private runtime entry" not in snapshot
    assert "private archive entry" not in snapshot
