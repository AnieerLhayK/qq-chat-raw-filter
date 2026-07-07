"""
phrase_miner.py — Automatic n-gram phrase discovery from my_blocks.

Designed to run as a pipeline stage after bucket_decision.  It scans the
user's own text (my_text) from candidates / micro_style / need_anonymize /
chaos_style / rejected buckets and discovers high-value 2-4 character Chinese
phrases via frequency, PMI (pointwise mutual information), and left/right
entropy.

Outputs:
  phrase_freq_{2,3,4}gram.jsonl  — raw n-gram frequency tables
  phrase_candidates.jsonl         — ranked candidate phrases with metrics
  phrase_mining_stats.json        — aggregate statistics
  phrase_mining_report.md         — human-readable report
  review_*_top.jsonl              — separated review files
"""

from __future__ import annotations

import json
import logging
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from qq_raw_filter.block_builder import MyBlock

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class NgramStats:
    """Metrics for one n-gram candidate."""
    phrase: str
    length: int
    freq: int = 0
    bucket_freq: Dict[str, int] = field(default_factory=lambda: {
        "candidates": 0, "micro_style": 0, "chaos_style": 0,
        "need_anonymize": 0, "rejected": 0,
    })
    min_pmi: float = 0.0
    left_entropy: float = 0.0
    right_entropy: float = 0.0
    min_entropy: float = 0.0
    style_keyness: float = 0.0
    chaos_rate: float = 0.0
    privacy_bucket_rate: float = 0.0
    suggested_label: str = "candidate_style_phrase"
    suggested_action: str = "review"
    status: str = "auto_candidate"
    source: str = "raw_filter_phrase_mining"
    reasons: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Chinese n-gram extraction
# ---------------------------------------------------------------------------

# Unicode ranges for CJK characters
_CJK_RANGE = set(range(0x4E00, 0x9FFF + 1)) | set(range(0x3400, 0x4DBF + 1))


def _is_cjk(ch: str) -> bool:
    return ord(ch) in _CJK_RANGE


def _extract_ngrams_from_text(text: str, n: int) -> List[str]:
    """Extract all n-grams from text that consist entirely of CJK characters.

    Non-CJK characters (punctuation, Latin, digits, emoji) act as delimiters.
    """
    # Normalise whitespace
    result: List[str] = []
    buffer: List[str] = []
    for ch in text:
        if _is_cjk(ch):
            buffer.append(ch)
        else:
            if len(buffer) >= n:
                for i in range(len(buffer) - n + 1):
                    result.append("".join(buffer[i:i + n]))
            buffer = []
    if len(buffer) >= n:
        for i in range(len(buffer) - n + 1):
            result.append("".join(buffer[i:i + n]))
    return result


def _build_char_freq(texts: List[str]) -> Counter:
    """Build character 1-gram frequency for PMI calculation."""
    counter: Counter = Counter()
    for text in texts:
        for ch in text:
            if _is_cjk(ch):
                counter[ch] += 1
    return counter


# ---------------------------------------------------------------------------
# PMI calculation (2-grams only)
# ---------------------------------------------------------------------------

def _calc_pmi_2gram(
    bigram: str,
    bigram_freq: int,
    unigram_freq: Counter,
    total_bigrams: int,
    total_unigrams: int,
) -> float:
    """PMI for a 2-gram: log2(p(xy) / (p(x) * p(y)))."""
    if bigram_freq < 2:
        return 0.0
    ch1, ch2 = bigram[0], bigram[1]
    p_xy = bigram_freq / total_bigrams
    p_x = unigram_freq.get(ch1, 1) / total_unigrams
    p_y = unigram_freq.get(ch2, 1) / total_unigrams
    if p_x <= 0 or p_y <= 0:
        return 0.0
    pmi = math.log2(p_xy / (p_x * p_y))
    return max(0.0, pmi)


# ---------------------------------------------------------------------------
# Left / right entropy
# ---------------------------------------------------------------------------

def _calc_entropy(surrounding: List[str]) -> float:
    """Shannon entropy of a list of surrounding characters."""
    if not surrounding:
        return 0.0
    n = len(surrounding)
    freq: Counter = Counter(surrounding)
    entropy = -sum((c / n) * math.log2(c / n) for c in freq.values())
    return entropy


def _build_surrounding(
    text: str, phrase: str
) -> Tuple[List[str], List[str]]:
    """Collect left-1 and right-1 characters for all occurrences of *phrase* in *text*."""
    lefts: List[str] = []
    rights: List[str] = []
    start = 0
    while True:
        pos = text.find(phrase, start)
        if pos == -1:
            break
        if pos > 0:
            prev_ch = text[pos - 1]
            if _is_cjk(prev_ch):
                lefts.append(prev_ch)
        end = pos + len(phrase)
        if end < len(text):
            next_ch = text[end]
            if _is_cjk(next_ch):
                rights.append(next_ch)
        start = pos + 1
    return lefts, rights


# ---------------------------------------------------------------------------
# Main mining engine
# ---------------------------------------------------------------------------

def mine_phrases(
    blocks: List[MyBlock],
    bucketed: Dict[str, List[MyBlock]],
    config: Dict[str, Any],
    lexicon: Dict[str, Any],
) -> Dict[str, Any]:
    """Run phrase mining on all blocks.

    Returns a dict with keys:
      phrase_freq_2gram  — List[NgramStats]
      phrase_freq_3gram  — List[NgramStats]
      phrase_freq_4gram  — List[NgramStats]
      phrase_candidates  — List[NgramStats] (ranked)
      stats              — aggregate dict
      review_phrases_top — top candidates for human review
      review_privacy     — privacy-relevant candidates
      review_chaos       — chaos-relevant candidates
      review_stopwords   — stopword candidates
    """
    pm_cfg = config.get("phrase_mining", {})
    enabled = pm_cfg.get("enabled", False)
    if not enabled:
        return {"enabled": False}

    min_n = pm_cfg.get("min_n", 2)
    max_n = pm_cfg.get("max_n", 4)
    min_freq = pm_cfg.get("min_freq", 5)
    min_pmi_2gram = pm_cfg.get("min_pmi_2gram", 2.0)
    min_pmi_3gram = pm_cfg.get("min_pmi_3gram", 3.0)
    min_pmi_4gram = pm_cfg.get("min_pmi_4gram", 4.0)
    min_entropy = pm_cfg.get("min_entropy", 0.5)
    top_k = pm_cfg.get("top_k_each_length", 500)
    scan_sources = pm_cfg.get("scan_sources", ["my_text"])

    bucket_weight = pm_cfg.get("bucket_weight", {})
    stoplist = lexicon.get("phrase_stoplist", set())

    # Build bucket -> blocks mapping (ensure candidates always exists)
    bucket_blocks: Dict[str, List[MyBlock]] = dict(bucketed) if bucketed else {}
    for bk in ("candidates", "micro_style", "need_anonymize", "chaos_style", "rejected"):
        bucket_blocks.setdefault(bk, [])

    # ---- Step 1: Extract texts per bucket ----
    bucket_texts: Dict[str, List[str]] = {}
    for bucket, blist in bucket_blocks.items():
        texts = []
        for blk in blist:
            for src in scan_sources:
                if src == "my_text":
                    t = blk.my_text
                elif src == "fragments":
                    t = " ".join(blk.fragments)
                elif src == "my_blocks":
                    t = " ".join(t.text for t in blk.my_turns if t.text)
                else:
                    t = ""
                if t:
                    texts.append(t)
        bucket_texts[bucket] = texts

    all_texts: List[str] = []
    for bucket in ("candidates", "micro_style", "need_anonymize", "chaos_style"):
        all_texts.extend(bucket_texts.get(bucket, []))
    all_texts.extend(bucket_texts.get("rejected", []))

    total_blocks_with_my_text = sum(1 for blk in blocks if blk.my_text)
    total_chars = sum(len(t) for t in all_texts)

    # ---- Step 2: Build n-gram counters per bucket ----
    all_ngrams: Dict[int, Counter] = {n: Counter() for n in range(min_n, max_n + 1)}
    bucket_ngrams: Dict[str, Dict[int, Counter]] = {}

    for bucket, texts in bucket_texts.items():
        bc = {n: Counter() for n in range(min_n, max_n + 1)}
        for text in texts:
            for n in range(min_n, max_n + 1):
                ngrams = _extract_ngrams_from_text(text, n)
                for ng in ngrams:
                    all_ngrams[n][ng] += 1
                    bc[n][ng] += 1
        bucket_ngrams[bucket] = bc

    # ---- Step 3: Build char 1-gram for PMI ----
    char_freq = _build_char_freq(all_texts)
    total_chars_count = sum(char_freq.values())
    total_bigrams = sum(all_ngrams[2].values())

    # ---- Step 4: Build stats entries ----
    all_candidates: Dict[int, List[NgramStats]] = {
        n: [] for n in range(min_n, max_n + 1)
    }

    pmi_thresholds = {
        2: min_pmi_2gram,
        3: min_pmi_3gram,
        4: min_pmi_4gram,
    }

    for n in range(min_n, max_n + 1):
        for phrase, freq in all_ngrams[n].most_common(top_k * 3):
            if freq < min_freq:
                continue
            if phrase in stoplist:
                continue

            # Bucket frequencies
            bf: Dict[str, int] = {bk: bucket_ngrams.get(bk, {}).get(n, Counter()).get(phrase, 0)
                                  for bk in bucket_blocks}
            total_in_buckets = sum(bf.values())

            # PMI (only for 2-grams; for longer n-grams use a simplified
            # version based on the average of internal bigram PMIs)
            min_pmi_val = 0.0
            if n == 2:
                min_pmi_val = _calc_pmi_2gram(
                    phrase, freq, char_freq, total_bigrams, total_chars_count
                )
            elif n >= 3:
                # For 3+ grams, compute the average of all internal bigram PMIs
                internal_bigrams = [phrase[i:i+2] for i in range(n - 1)]
                pmi_vals = [
                    _calc_pmi_2gram(bg, all_ngrams[2].get(bg, 0),
                                    char_freq, total_bigrams, total_chars_count)
                    for bg in internal_bigrams
                ]
                min_pmi_val = sum(pmi_vals) / len(pmi_vals) if pmi_vals else 0.0

            if min_pmi_val < pmi_thresholds.get(n, 2.0):
                continue

            # Left / right entropy
            all_lefts: List[str] = []
            all_rights: List[str] = []
            for text in all_texts:
                lefts, rights = _build_surrounding(text, phrase)
                all_lefts.extend(lefts)
                all_rights.extend(rights)
            left_ent = _calc_entropy(all_lefts)
            right_ent = _calc_entropy(all_rights)
            min_ent = min(left_ent, right_ent)
            if min_ent < min_entropy:
                continue

            # Bucket-aware metrics
            c_count = bf.get("candidates", 0)
            micro_count = bf.get("micro_style", 0)
            chaos_count = bf.get("chaos_style", 0)
            priv_count = bf.get("need_anonymize", 0)
            rej_count = bf.get("rejected", 0)

            weighted_candidate = c_count * bucket_weight.get("candidates", 1.0)
            weighted_micro = micro_count * bucket_weight.get("micro_style", 1.2)
            weighted_chaos = chaos_count * bucket_weight.get("chaos_style", 0.5)
            weighted_priv = priv_count * bucket_weight.get("need_anonymize", 0.4)
            weighted_rej = rej_count * bucket_weight.get("rejected", -0.5)

            # Style keyness: how much this phrase characterises "good" buckets
            good_weight = weighted_candidate + weighted_micro
            bad_weight = weighted_chaos + weighted_priv + max(0, weighted_rej)
            if bad_weight <= 0:
                style_keyness = good_weight / max(1, total_in_buckets * 0.1) * 2
            else:
                style_keyness = good_weight / max(1, bad_weight)

            chaos_rate = chaos_count / max(1, total_in_buckets)
            privacy_rate = priv_count / max(1, total_in_buckets)

            # Suggested label
            if chaos_rate > 0.5 and style_keyness < 2:
                suggested_label = "chaos_phrase"
            elif privacy_rate > 0.3:
                suggested_label = "privacy_phrase"
            elif style_keyness > 2.0:
                suggested_label = "candidate_style_phrase"
            else:
                suggested_label = "ambiguous_phrase"

            # Suggested action
            if chaos_rate > 0.7 or (rej_count > c_count and freq < 10):
                suggested_action = "skip"
            elif privacy_rate > 0.4:
                suggested_action = "privacy_review"
            elif style_keyness > 1.5 and chaos_rate < 0.3:
                suggested_action = "review"
            else:
                suggested_action = "review"

            reasons = []
            if c_count > 0 and rej_count == 0:
                reasons.append("high_candidate_frequency")
            if c_count > rej_count * 2 and rej_count > 0:
                reasons.append("low_rejected_frequency")
            if min_pmi_val > min_pmi_2gram * 2:
                reasons.append("stable_ngram")
            if min_ent > 1.5:
                reasons.append("high_entropy")
            if chaos_rate < 0.1:
                reasons.append("low_chaos")

            stats = NgramStats(
                phrase=phrase,
                length=n,
                freq=freq,
                bucket_freq=bf,
                min_pmi=round(min_pmi_val, 2),
                left_entropy=round(left_ent, 4),
                right_entropy=round(right_ent, 4),
                min_entropy=round(min_ent, 4),
                style_keyness=round(style_keyness, 2),
                chaos_rate=round(chaos_rate, 4),
                privacy_bucket_rate=round(privacy_rate, 4),
                suggested_label=suggested_label,
                suggested_action=suggested_action,
                reasons=reasons,
            )
            all_candidates[n].append(stats)

    # Truncate each length to top_k
    for n in range(min_n, max_n + 1):
        all_candidates[n].sort(key=lambda x: (-x.style_keyness, -x.freq))
        all_candidates[n] = all_candidates[n][:top_k]

    # ---- Step 5: Build output structures ----
    total_candidates = sum(len(v) for v in all_candidates.values())

    # Separate review files
    review_top = []
    review_privacy = []
    review_chaos = []
    review_stopwords = []

    for n in range(min_n, max_n + 1):
        for s in all_candidates[n]:
            if s.suggested_action == "privacy_review":
                review_privacy.append(s)
            elif s.chaos_rate > 0.5:
                review_chaos.append(s)
            else:
                review_top.append(s)
            # Check if the phrase should also be a stopword candidate
            if (s.freq > min_freq * 5
                    and s.min_pmi < min_pmi_2gram * 0.6
                    and s.min_entropy < 0.8):
                review_stopwords.append(s)

    review_top.sort(key=lambda x: -x.style_keyness)
    review_privacy.sort(key=lambda x: -x.privacy_bucket_rate)
    review_chaos.sort(key=lambda x: -x.chaos_rate)
    review_stopwords.sort(key=lambda x: -x.freq)

    stats = {
        "enabled": True,
        "total_blocks_scanned": total_blocks_with_my_text,
        "total_chars_scanned": total_chars,
        "min_freq": min_freq,
        "min_entropy": min_entropy,
        "pmi_thresholds": {str(n): pmi_thresholds[n] for n in range(min_n, max_n + 1)},
        "total_candidates": total_candidates,
        "by_length": {str(n): len(all_candidates[n]) for n in range(min_n, max_n + 1)},
        "candidates_for_review": len(review_top),
        "privacy_candidates": len(review_privacy),
        "chaos_candidates": len(review_chaos),
        "stopword_candidates": len(review_stopwords),
        "stoplist_filtered": len([p for p in stoplist if any(
            s.phrase == p for n in range(min_n, max_n + 1) for s in all_candidates[n]
        )]),
    }

    return {
        "phrase_freq_2gram": [_asdict(s) for s in all_candidates.get(2, [])],
        "phrase_freq_3gram": [_asdict(s) for s in all_candidates.get(3, [])],
        "phrase_freq_4gram": [_asdict(s) for s in all_candidates.get(4, [])],
        "phrase_candidates": [
            _asdict(s) for n in range(min_n, max_n + 1)
            for s in all_candidates[n]
            if s.suggested_action in ("review",)
        ],
        "stats": stats,
        "review_phrases_top": [_asdict(s) for s in review_top[:100]],
        "review_privacy_terms": [_asdict(s) for s in review_privacy[:100]],
        "review_chaos_terms": [_asdict(s) for s in review_chaos[:100]],
        "review_stopword_candidates": [_asdict(s) for s in review_stopwords[:100]],
    }


def _asdict(s: NgramStats) -> Dict[str, Any]:
    """Convert NgramStats to a plain dict with consistent field order."""
    return {
        "phrase": s.phrase,
        "length": s.length,
        "freq": s.freq,
        "bucket_freq": s.bucket_freq,
        "min_pmi": s.min_pmi,
        "left_entropy": s.left_entropy,
        "right_entropy": s.right_entropy,
        "min_entropy": s.min_entropy,
        "style_keyness": s.style_keyness,
        "chaos_rate": s.chaos_rate,
        "privacy_bucket_rate": s.privacy_bucket_rate,
        "suggested_label": s.suggested_label,
        "suggested_action": s.suggested_action,
        "status": s.status,
        "source": s.source,
        "reasons": s.reasons,
    }


# ---------------------------------------------------------------------------
# Report generator
# ---------------------------------------------------------------------------

def generate_phrase_report(
    mining_result: Dict[str, Any],
    config: Dict[str, Any],
    run_dir: Path,
) -> None:
    """Generate phrase_mining_report.md in run_dir."""
    if not mining_result.get("enabled", True):
        return

    stats = mining_result.get("stats", {})
    candidates = mining_result.get("phrase_candidates", [])
    top_phrases = mining_result.get("review_phrases_top", [])
    privacy = mining_result.get("review_privacy_terms", [])
    chaos = mining_result.get("review_chaos_terms", [])
    stopwords_rv = mining_result.get("review_stopword_candidates", [])

    lines: List[str] = []
    lines.append("# Phrase Mining Report")
    lines.append("")
    lines.append(f"- Scanned {stats.get('total_blocks_scanned', 0)} blocks, "
                 f"{stats.get('total_chars_scanned', 0)} chars")
    lines.append(f"- min_freq={stats.get('min_freq', 5)}, "
                 f"min_entropy={stats.get('min_entropy', 0.5)}")
    lines.append(f"- PMI thresholds: {stats.get('pmi_thresholds', {})}")
    lines.append("")

    lines.append("## Overview")
    lines.append("")
    lines.append(f"- Total n-gram candidates: {stats.get('total_candidates', 0)}")
    by_len = stats.get("by_length", {})
    for n in sorted(by_len.keys()):
        lines.append(f"  - {n}-gram: {by_len[n]}")
    lines.append(f"- Stoplist filtered: {stats.get('stoplist_filtered', 0)}")
    lines.append("")

    lines.append("## Top Style Phrase Candidates (by style_keyness)")
    lines.append("")
    lines.append("| Phrase | Len | Freq | Keyness | PMI | Entropy | Bucket Freq |")
    lines.append("|--------|-----|------|---------|-----|---------|-------------|")
    for s in top_phrases[:30]:
        bf = s.get("bucket_freq", {})
        bf_str = f"C:{bf.get('candidates',0)} M:{bf.get('micro_style',0)} X:{bf.get('chaos_style',0)} P:{bf.get('need_anonymize',0)} R:{bf.get('rejected',0)}"
        lines.append(
            f"| {s['phrase']} | {s['length']} | {s['freq']} "
            f"| {s['style_keyness']} | {s['min_pmi']} "
            f"| {s['min_entropy']} | {bf_str} |"
        )
    lines.append("")

    if privacy:
        lines.append("## Privacy Risk Phrases")
        lines.append("")
        lines.append("| Phrase | Freq | Privacy Rate |")
        lines.append("|--------|------|-------------|")
        for s in privacy[:20]:
            lines.append(
                f"| {s['phrase']} | {s['freq']} | {s['privacy_bucket_rate']} |"
            )
        lines.append("")

    if chaos:
        lines.append("## Chaos / Abstract Phrases")
        lines.append("")
        lines.append("| Phrase | Freq | Chaos Rate |")
        lines.append("|--------|------|------------|")
        for s in chaos[:20]:
            lines.append(
                f"| {s['phrase']} | {s['freq']} | {s['chaos_rate']} |"
            )
        lines.append("")

    if stopwords_rv:
        lines.append("## Stopword Candidates")
        lines.append("")
        lines.append("| Phrase | Freq | PMI | Entropy |")
        lines.append("|--------|------|-----|---------|")
        for s in stopwords_rv[:20]:
            lines.append(
                f"| {s['phrase']} | {s['freq']} | {s['min_pmi']} | {s['min_entropy']} |"
            )
        lines.append("")

    lines.append("## Next Steps")
    lines.append("")
    lines.append("1. Review **Top Style Phrase Candidates** — add high-value ones to `phrase_bank.jsonl`.")
    lines.append("2. Check **Privacy Risk Phrases** — handle or mask before including in skill.")
    lines.append("3. Check **Chaos / Abstract Phrases** — decide if any belong in `chaos_lexicon`.")
    lines.append("4. Check **Stopword Candidates** — add confirmed stopwords to `phrase_stoplist.txt`.")
    lines.append("5. Run with `--update-lexicon` to merge candidates into `phrase_candidates.jsonl`.")
    lines.append("")

    (run_dir / "phrase_mining_report.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )
    logger.info("Phrase mining report written to phrase_mining_report.md")
