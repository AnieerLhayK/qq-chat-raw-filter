"""
filter_applier.py — Apply textual filters to MyBlock objects.

- drop_sentence_words: entire block is dropped (→ rejected) if my_text contains
  any phrase from the blocklist.
- mask_words: matched substrings in my_text are replaced with [MASKED].
"""

from __future__ import annotations

import logging
import re
from typing import List

from block_builder import MyBlock

logger = logging.getLogger(__name__)


def _compile_words(words: List[str]) -> List[re.Pattern]:
    """Compile word list to case-insensitive regex patterns."""
    return [re.compile(re.escape(w), re.IGNORECASE) for w in words]


def apply_drop_sentence_words(blocks: List[MyBlock], drop_words: List[str]) -> int:
    """Drop blocks whose my_text contains any drop_sentence_word.

    Marks their bucket as 'rejected' with reason 'dropped_by_sentence_word:<word>'.

    Returns:
        Number of blocks dropped.
    """
    if not drop_words:
        return 0

    patterns = _compile_words(drop_words)
    dropped = 0

    for block in blocks:
        if block.bucket and block.bucket != "candidates":
            # Only apply to blocks that aren't already rejected or chaos
            continue
        for i, pat in enumerate(patterns):
            if pat.search(block.my_text):
                block.bucket = "rejected"
                block.reasons.append(f"dropped_by_sentence_word:{drop_words[i]}")
                dropped += 1
                break

    return dropped


def apply_masklist(blocks: List[MyBlock], mask_words: List[str]) -> int:
    """Replace occurrences of mask_words in block.my_text with [MASKED].

    Operates on blocks that are NOT already rejected (masking rejected blocks
    is unnecessary since they won't be output).

    Returns:
        Number of masking operations performed.
    """
    if not mask_words:
        return 0

    patterns = _compile_words(mask_words)
    n_masked = 0

    for block in blocks:
        if block.bucket == "rejected":
            continue
        for pat in patterns:
            new_text, subs = pat.subn("[MASKED]", block.my_text)
            if subs > 0:
                block.my_text = new_text
                n_masked += subs
                block.reasons.append(f"masked_word:{subs}")

    return n_masked
