"""
scorer.py — Score MyBlock objects on four dimensions: style, privacy, junk, chaos.

Each scorer returns a float score (higher = more of that dimension).
Used by bucket.py for classification decisions.
"""

from __future__ import annotations

import re
import logging
from typing import Any, Dict, List, Pattern

from block_builder import MyBlock

logger = logging.getLogger(__name__)

_EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_URL_PATTERN = re.compile(r"https?://[^\s()<>\"']+|(?:www\.)[^\s()<>\"']+", re.IGNORECASE)
_PHONE_PATTERN = re.compile(r"1[3-9]\d{9}")
_ID_CARD_PATTERN = re.compile(r"[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]")
_QQ_PATTERN = re.compile(r"\b[1-9]\d{4,10}\b")
_PUNCT_ONLY = re.compile(r"^[^\w\s一-鿿]+$")

_NOT_X_BUT_Y = re.compile(r"不是[^。]*而是")
_FIRST_THEN = re.compile(r"先[^。]*再[^。]*")
_ANALYSIS_KEY = re.compile(r"主要是|关键是|本质上|说白了")
_I_FEEL = re.compile(r"我感觉|我个人|我认为|我觉得")


def _compile_words(words: List[str]) -> List[Pattern]:
    return [re.compile(re.escape(w), re.IGNORECASE) for w in words]


def score_style(block: MyBlock, config: Dict[str, Any]) -> float:
    """Score the block for expression/analysis style. 0-10."""
    text = block.my_text
    if not text:
        return 0

    score = 0.0
    metrics = block.metrics
    good_chars = config.get("my_block", {}).get("good_my_block_chars", 120)
    min_chars = config.get("my_block", {}).get("min_my_block_chars", 50)

    char_count = metrics.get("my_char_count", 0)
    if char_count >= good_chars:
        score += 2.0
    elif char_count >= min_chars:
        score += 1.0

    if metrics.get("my_turn_count", 0) >= 3:
        score += 1.0
    if metrics.get("my_turn_count", 0) >= 5:
        score += 0.5

    analysis_words = config.get("style_markers", {}).get("analysis", [])
    analysis_hits = sum(1 for w in analysis_words if w in text)
    score += min(analysis_hits * 2.0, 6.0)

    tone_words = config.get("style_markers", {}).get("tone", [])
    tone_hits = sum(1 for w in tone_words if w in text)
    score += min(tone_hits * 1.0, 3.0)

    if _NOT_X_BUT_Y.search(text):
        score += 2.0
    if _FIRST_THEN.search(text):
        score += 1.5
    if _ANALYSIS_KEY.search(text):
        score += 1.5
    if _I_FEEL.search(text):
        score += 1.0

    sentences = [s for s in re.split(r"[。！？\n]", text) if s.strip()]
    if len(sentences) >= 3:
        score += 1.0

    total_frags = sum(len(t.fragments) for t in block.my_turns)
    if total_frags >= 5 and char_count >= 100:
        score += 1.5

    return round(min(score, 15.0), 1)


def score_privacy(block: MyBlock, config: Dict[str, Any]) -> float:
    """Score privacy risk. 0-10. Higher = more risk."""
    text = block.my_text
    if not text:
        return 0

    score = 0.0
    privacy_cfg = config.get("privacy", {})

    high_risk = _compile_words(privacy_cfg.get("high_risk_words", []))
    for pat in high_risk:
        if pat.search(text):
            score += 3.0

    medium_risk = _compile_words(privacy_cfg.get("medium_risk_words", []))
    for pat in medium_risk:
        if pat.search(text):
            score += 1.5

    if _EMAIL_PATTERN.search(text):
        score += 4.0
    if _PHONE_PATTERN.search(text):
        score += 4.0
    if _ID_CARD_PATTERN.search(text):
        score += 5.0
    if _URL_PATTERN.search(text):
        score += 2.0
    if _QQ_PATTERN.search(text):
        for m in _QQ_PATTERN.findall(text):
            if len(m) >= 8:
                score += 2.0

    return round(min(score, 15.0), 1)


def score_junk(block: MyBlock, config: Dict[str, Any]) -> float:
    """Score junk/noise level. 0-10."""
    text = block.my_text
    if not text:
        return 0

    score = 0.0
    metrics = block.metrics

    if block.my_turns:
        non_text_count = sum(1 for t in block.my_turns
                             if t.msg_type not in ("text", "reply"))
        total_frags = sum(len(t.fragments) for t in block.my_turns)
        if total_frags > 0 and non_text_count / total_frags > 0.5:
            score += 3.0

    if _PUNCT_ONLY.match(text):
        score += 5.0

    short_ratio = metrics.get("short_fragment_ratio", 0)
    max_sf = config.get("ratio", {}).get("max_short_fragment_ratio", 0.70)
    if short_ratio > max_sf:
        score += 3.0

    if len(text) >= 10:
        unique_ratio = len(set(text)) / len(text)
        if unique_ratio < 0.2:
            score += 4.0

    max_block = config.get("my_block", {}).get("max_block_chars", 1200)
    if metrics.get("total_char_count", 0) > max_block:
        score += 3.0

    li_phrases = config.get("light_interruption", {}).get("phrases", [])
    li_hits = sum(text.count(p) for p in li_phrases)
    if li_hits >= 3:
        score += min(li_hits * 0.5, 3.0)

    return round(min(score, 10.0), 1)


def score_chaos(block: MyBlock, config: Dict[str, Any]) -> float:
    """Score chaos/abusive level. 0-10."""
    text = block.my_text
    if not text:
        return 0

    score = 0.0
    chaos_cfg = config.get("chaos", {})

    mild = _compile_words(chaos_cfg.get("mild_words", []))
    mild_hits = sum(1 for pat in mild if pat.search(text))
    score += min(mild_hits * 1.0, 3.0)

    strong = _compile_words(chaos_cfg.get("strong_words", []))
    strong_hits = sum(1 for pat in strong if pat.search(text))
    score += min(strong_hits * 2.0, 6.0)

    lines = text.split("\n")
    if len(lines) >= 5:
        unique_lines = len(set(lines))
        if unique_lines / len(lines) < 0.5:
            score += 2.0

    return round(min(score, 10.0), 1)


def score_all(block: MyBlock, config: Dict[str, Any]) -> Dict[str, float]:
    """Compute all four scores for a block."""
    return {
        "style_score": score_style(block, config),
        "privacy_score": score_privacy(block, config),
        "junk_score": score_junk(block, config),
        "chaos_score": score_chaos(block, config),
    }
