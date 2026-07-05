"""
style_tagger.py — Compute style_tags for each MyBlock.

Tags are stored on the block as ``block.style_tags: List[str]``.

Available tags:
  - analysis_explaining
  - not_x_but_y_pattern
  - qq_fragmented_long_speech
  - short_reaction
  - joke_abstract
  - argument
  - low_signal
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Tuple

from block_builder import MyBlock

logger = logging.getLogger(__name__)

_NOT_X_BUT_Y = re.compile(r"不是[^。]*而是")

# Tag computation thresholds
_MIN_TURN_COUNT_FOR_FRAGMENTED = 4
_MIN_CHARS_FOR_FRAGMENTED = 200
_MAX_CHARS_FOR_SHORT_REACTION = 30
_MIN_CHAOS_FOR_JOKE = 3
_MIN_STYLE_FOR_JOKE = 3
_MIN_TURN_COUNT_FOR_ARGUMENT = 3


def _get_tag_markers(config: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """Get argument/analysis tag markers from lexicon (preferred) or inline TOML."""
    lex = config.get("_lexicon")
    if lex:
        return (
            lex.get("argument_markers", []),
            lex.get("analysis_tag_markers", []),
        )
    st = config.get("style_tags", {})
    return (
        st.get("argument_markers", ["但是", "不过", "然而", "其实"]),
        st.get("analysis_markers", ["我感觉", "我认为", "我觉得", "本质上", "说白了"]),
    )


def tag_style(blocks: List[MyBlock], config: Dict[str, Any]) -> int:
    """Compute and attach style_tags to each block.

    Ensures each block has a ``style_tags`` attribute (list of strings).

    Returns:
        Number of blocks that received at least one tag.
    """
    if not config.get("style_tags", {}).get("enable", True):
        for block in blocks:
            block.style_tags = []
        return 0

    arg_markers, analysis_markers = _get_tag_markers(config)
    _arg_patterns = [re.compile(re.escape(m)) for m in arg_markers]
    _analysis_patterns = [re.compile(re.escape(m)) for m in analysis_markers]

    tagged = 0

    for block in blocks:
        text = block.my_text
        scores = block.scores
        metrics = block.metrics
        tags: List[str] = []

        style = scores.get("style_score", 0)
        chaos = scores.get("chaos_score", 0)
        junk = scores.get("junk_score", 0)
        char_count = metrics.get("my_char_count", 0)
        turn_count = metrics.get("my_turn_count", 0)

        # analysis_explaining — has analysis discourse markers
        if any(p.search(text) for p in _analysis_patterns):
            tags.append("analysis_explaining")

        # not_x_but_y_pattern — structural contrast
        if _NOT_X_BUT_Y.search(text):
            tags.append("not_x_but_y_pattern")

        # qq_fragmented_long_speech — many short messages forming a long block
        if turn_count >= _MIN_TURN_COUNT_FOR_FRAGMENTED and char_count >= _MIN_CHARS_FOR_FRAGMENTED:
            tags.append("qq_fragmented_long_speech")

        # short_reaction — brief tone-only response
        if char_count <= _MAX_CHARS_FOR_SHORT_REACTION and style >= 2:
            tags.append("short_reaction")

        # joke_abstract — has chaos but enough style to be expressive
        if chaos >= _MIN_CHAOS_FOR_JOKE and style >= _MIN_STYLE_FOR_JOKE:
            tags.append("joke_abstract")

        # argument — multi-turn with contrast markers
        if turn_count >= _MIN_TURN_COUNT_FOR_ARGUMENT:
            if any(p.search(text) for p in _arg_patterns):
                tags.append("argument")

        # low_signal — neither styleful nor junk
        if style < 2 and junk < 3:
            tags.append("low_signal")

        if tags:
            tagged += 1

        block.style_tags = tags

    return tagged
