"""
lexicon_auditor.py — Frequency audit of archived lexicon terms against
real pipeline data.

Runs as a pipeline stage after bucket_decision.  For each term in the
archive lexicon, counts occurrences across all buckets and classifies
the term for promotion, demotion, or removal from the active lexicon.

Outputs:
  legacy_lexicon_frequency_audit.jsonl — per-term audit data
  legacy_lexicon_audit_report.md      — human-readable summary
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from qq_raw_filter.block_builder import MyBlock

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Classification thresholds
# ---------------------------------------------------------------------------
MIN_FREQ_PROMOTE = 5         # Minimum total frequency to consider promotion
MIN_CANDIDATE_RATE = 0.3     # Minimum candidate+micro_style / total ratio
MAX_REJECTED_RATE = 0.3      # Maximum rejected / total ratio for promotion
MIN_FREQ_CANDIDATE = 2       # Minimum frequency to stay as candidate
MAX_REJECTED_RATE_DEMOTE = 0.5  # Above this, demote regardless of frequency


def _classify_term(
    freq_total: int,
    bucket_freq: Dict[str, int],
    old_category: str,
) -> Dict[str, Any]:
    """Classify an archived term for promotion/demotion/removal.

    Returns a dict with suggested_status and reasons.
    """
    c_count = bucket_freq.get("candidates", 0)
    m_count = bucket_freq.get("micro_style", 0)
    r_count = bucket_freq.get("rejected", 0)
    total = max(freq_total, 1)

    candidate_rate = (c_count + m_count) / total
    rejected_rate = r_count / total

    # Simple keyness: how much more candidate+micro than rejected+chaos+privacy
    good = c_count + m_count
    bad = r_count + bucket_freq.get("chaos_style", 0) + bucket_freq.get("need_anonymize", 0)
    if bad <= 0:
        style_keyness = good / max(1, total * 0.1) * 2.0
    else:
        style_keyness = good / max(1, bad)

    reasons: List[str] = []

    # Determine if this is a privacy or chaos category
    is_privacy = old_category.startswith("privacy.")
    is_chaos = old_category.startswith("chaos.")
    is_chaos_strong = "strong" in old_category
    is_drop_sentence = old_category.startswith("filter.drop_sentence")
    is_mask = old_category.startswith("filter.mask")
    is_stoplist = old_category.startswith("phrase_mining.stoplist")

    # Classification logic
    if freq_total == 0:
        suggested = "demote_or_remove"
        reasons.append("zero_frequency")
    elif is_privacy:
        # Privacy words are never promoted to active style lexicon
        if freq_total >= MIN_FREQ_PROMOTE:
            suggested = "keep_in_privacy_lexicon"
            reasons.append("verified_privacy_term")
        else:
            suggested = "demote_or_remove"
            reasons.append("low_frequency_privacy_term")
    elif is_chaos_strong:
        # Strong chaos words always isolated, never in active style
        suggested = "keep_in_chaos_lexicon"
        reasons.append("strong_chaos_isolated")
    elif is_drop_sentence or is_mask:
        # Filter words: keep if they still match
        if freq_total >= MIN_FREQ_PROMOTE:
            suggested = "promote_to_active"
            reasons.append("verified_filter_word")
        elif freq_total >= MIN_FREQ_CANDIDATE:
            suggested = "keep_as_candidate"
            reasons.append("low_frequency_filter_word")
        else:
            suggested = "demote_or_remove"
            reasons.append("unused_filter_word")
    elif is_stoplist:
        # Stoplist words assessed separately
        if freq_total >= 10:
            suggested = "keep_in_stoplist"
            reasons.append("high_frequency_stopword")
        else:
            suggested = "demote_or_remove"
            reasons.append("low_impact_stopword")
    elif freq_total >= MIN_FREQ_PROMOTE and candidate_rate >= MIN_CANDIDATE_RATE and rejected_rate <= MAX_REJECTED_RATE:
        suggested = "promote_to_active"
        reasons.append("high_frequency")
        reasons.append("high_candidate_presence")
        reasons.append("low_rejected_presence")
    elif freq_total >= MIN_FREQ_CANDIDATE:
        if rejected_rate > MAX_REJECTED_RATE_DEMOTE:
            suggested = "demote_or_remove"
            reasons.append("high_rejected_presence")
        else:
            suggested = "keep_as_candidate"
            reasons.append("moderate_frequency")
        if candidate_rate < MIN_CANDIDATE_RATE:
            reasons.append("low_candidate_ratio")
    else:
        suggested = "demote_or_remove"
        if rejected_rate > MAX_REJECTED_RATE_DEMOTE:
            reasons.append("high_rejected_presence")
        reasons.append("very_low_frequency")

    if style_keyness > 2.0 and suggested != "promote_to_active":
        reasons.append("notable_style_keyness_despite_other_factors")
    if candidate_rate > 0.5:
        reasons.append("strong_candidate_signal")

    return {
        "candidate_rate": round(candidate_rate, 4),
        "rejected_rate": round(rejected_rate, 4),
        "style_keyness": round(style_keyness, 2),
        "suggested_status": suggested,
        "reasons": reasons,
    }


def audit_legacy_lexicon(
    blocks: List[MyBlock],
    bucketed: Dict[str, List[MyBlock]],
    archive_terms: List[Dict[str, Any]],
    config: Dict[str, Any],
    run_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run frequency audit of archived lexicon terms against classified blocks.

    Args:
        blocks: All MyBlock objects (scored but not necessarily bucketed)
        bucketed: Dict of bucket_name -> [MyBlock] from bucket_decision
        archive_terms: Flat list of archived term dicts from load_archive_terms()
        config: Pipeline config dict
        run_dir: Optional output directory for writing audit files

    Returns:
        Dict with audit_entries, stats, and file paths written
    """
    if not archive_terms:
        logger.info("Audit: no archive terms to audit")
        return {"audit_entries": [], "stats": {"total_audited": 0}}

    # Build term list (unique phrases with their metadata)
    term_map: Dict[str, Dict[str, Any]] = {}
    for entry in archive_terms:
        phrase = entry.get("phrase", "").strip()
        if not phrase:
            continue
        if phrase not in term_map:
            term_map[phrase] = {
                "phrase": phrase,
                "source_files": [],
                "old_categories": [],
                "labels": [],
            }
        info = term_map[phrase]
        sf = entry.get("source_file", "unknown")
        if sf not in info["source_files"]:
            info["source_files"].append(sf)
        cat = entry.get("old_category", "unknown")
        if cat not in info["old_categories"]:
            info["old_categories"].append(cat)
        label = entry.get("label", entry.get("severity", entry.get("risk", "")))
        if label and label not in info["labels"]:
            info["labels"].append(label)

    # Build block texts per bucket for efficient scanning
    bucket_texts: Dict[str, List[str]] = {}
    for bk in ("candidates", "micro_style", "need_anonymize", "chaos_style", "rejected"):
        blist = bucketed.get(bk, [])
        bucket_texts[bk] = [b.my_text for b in blist if b.my_text]

    all_my_texts: List[str] = []
    for b in blocks:
        if b.my_text:
            all_my_texts.append(b.my_text)

    # Count per-bucket occurrences for each term
    audit_entries: List[Dict[str, Any]] = []
    total_terms = len(term_map)
    logger.info("Audit: scanning %d unique archived terms across %d blocks",
                total_terms, len(blocks))

    for idx, (phrase, info) in enumerate(term_map.items()):
        if idx > 0 and idx % 100 == 0:
            logger.debug("Audit progress: %d/%d terms", idx, total_terms)

        bucket_freq: Dict[str, int] = {}
        for bk, texts in bucket_texts.items():
            count = sum(1 for t in texts if phrase in t)
            bucket_freq[bk] = count

        freq_total = sum(bucket_freq.values())

        # Count distinct my_blocks containing this phrase
        my_block_count = sum(1 for t in all_my_texts if phrase in t)

        # Primary category (use first one)
        primary_category = info["old_categories"][0] if info["old_categories"] else "unknown"
        primary_source = info["source_files"][0] if info["source_files"] else "unknown"

        classification = _classify_term(freq_total, bucket_freq, primary_category)

        entry = {
            "term": phrase,
            "old_category": primary_category,
            "all_categories": info["old_categories"],
            "source_file": primary_source,
            "all_source_files": info["source_files"],
            "labels": info["labels"],
            "freq_total": freq_total,
            "bucket_freq": bucket_freq,
            "my_block_count": my_block_count,
            **classification,
        }
        audit_entries.append(entry)

    # Sort by frequency descending
    audit_entries.sort(key=lambda x: -x["freq_total"])

    # Aggregate stats
    promote_count = sum(1 for e in audit_entries if e["suggested_status"] == "promote_to_active")
    candidate_count = sum(1 for e in audit_entries if e["suggested_status"] == "keep_as_candidate")
    demote_count = sum(1 for e in audit_entries if e["suggested_status"] == "demote_or_remove")
    privacy_count = sum(1 for e in audit_entries if "privacy" in e.get("suggested_status", ""))
    chaos_count = sum(1 for e in audit_entries if "chaos" in e.get("suggested_status", ""))
    filter_count = sum(1 for e in audit_entries
                       if e.get("old_category", "").startswith("filter."))
    zero_freq = sum(1 for e in audit_entries if e["freq_total"] == 0)

    stats = {
        "total_terms_audited": len(audit_entries),
        "total_occurrences": sum(e["freq_total"] for e in audit_entries),
        "promote_to_active": promote_count,
        "keep_as_candidate": candidate_count,
        "demote_or_remove": demote_count,
        "privacy_terms": privacy_count,
        "chaos_terms": chaos_count,
        "filter_terms": filter_count,
        "zero_frequency_terms": zero_freq,
        "by_source_file": {},
        "by_old_category": {},
    }

    for e in audit_entries:
        sf = e["source_file"]
        stats["by_source_file"][sf] = stats["by_source_file"].get(sf, 0) + 1
        cat = e["old_category"]
        stats["by_old_category"][cat] = stats["by_old_category"].get(cat, 0) + 1

    # Write outputs if run_dir provided
    output_paths: Dict[str, str] = {}
    if run_dir:
        audit_cfg = config.get("lexicon", {}).get("audit", {})

        if audit_cfg.get("output_audit_file", True):
            audit_file = run_dir / "legacy_lexicon_frequency_audit.jsonl"
            with audit_file.open("w", encoding="utf-8") as f:
                for entry in audit_entries:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            output_paths["audit_jsonl"] = str(audit_file)
            logger.info("Audit written: %s (%d entries)", audit_file, len(audit_entries))

        if audit_cfg.get("output_audit_report", True):
            report_path = run_dir / "legacy_lexicon_audit_report.md"
            generate_audit_report(audit_entries, stats, report_path)
            output_paths["audit_report"] = str(report_path)
            logger.info("Audit report: %s", report_path)

    return {
        "audit_entries": audit_entries,
        "stats": stats,
        "output_paths": output_paths,
    }


# ---------------------------------------------------------------------------
# Report generator
# ---------------------------------------------------------------------------
def generate_audit_report(
    audit_entries: List[Dict[str, Any]],
    stats: Dict[str, Any],
    report_path: Path,
) -> None:
    """Generate legacy_lexicon_audit_report.md."""
    lines: List[str] = []
    lines.append("# Legacy Lexicon Frequency Audit Report")
    lines.append("")
    lines.append(f"- Total terms audited: **{stats['total_terms_audited']}**")
    lines.append(f"- Total occurrences in data: **{stats['total_occurrences']}**")
    lines.append(f"- Zero-frequency terms: **{stats.get('zero_frequency_terms', 0)}**")
    lines.append("")

    lines.append("## Classification Summary")
    lines.append("")
    lines.append("| Status | Count |")
    lines.append("|--------|-------|")
    lines.append(f"| promote_to_active | {stats.get('promote_to_active', 0)} |")
    lines.append(f"| keep_as_candidate | {stats.get('keep_as_candidate', 0)} |")
    lines.append(f"| demote_or_remove | {stats.get('demote_or_remove', 0)} |")
    lines.append(f"| privacy_terms | {stats.get('privacy_terms', 0)} |")
    lines.append(f"| chaos_terms | {stats.get('chaos_terms', 0)} |")
    lines.append(f"| filter_terms | {stats.get('filter_terms', 0)} |")
    lines.append("")

    lines.append("## By Source File")
    lines.append("")
    lines.append("| File | Terms |")
    lines.append("|------|-------|")
    for sf, count in sorted(stats.get("by_source_file", {}).items()):
        lines.append(f"| {sf} | {count} |")
    lines.append("")

    lines.append("## By Old Category")
    lines.append("")
    lines.append("| Category | Terms |")
    lines.append("|----------|-------|")
    for cat, count in sorted(stats.get("by_old_category", {}).items()):
        lines.append(f"| {cat} | {count} |")
    lines.append("")

    # Top promoted
    promoted = [e for e in audit_entries if e["suggested_status"] == "promote_to_active"][:20]
    if promoted:
        lines.append("## Top 20 Promoted to Active")
        lines.append("")
        lines.append("| Term | Freq | Cand | Micro | Chaos | Priv | Rej | Keyness |")
        lines.append("|------|------|------|-------|-------|------|-----|---------|")
        for e in promoted:
            bf = e["bucket_freq"]
            lines.append(
                f"| {e['term']} | {e['freq_total']} "
                f"| {bf.get('candidates', 0)} | {bf.get('micro_style', 0)} "
                f"| {bf.get('chaos_style', 0)} | {bf.get('need_anonymize', 0)} "
                f"| {bf.get('rejected', 0)} | {e['style_keyness']} |"
            )
        lines.append("")

    # Top demoted
    demoted = [e for e in audit_entries if e["suggested_status"] == "demote_or_remove"][:20]
    if demoted:
        lines.append("## Top 20 Demoted / Removed")
        lines.append("")
        lines.append("| Term | Freq | Category | Reasons |")
        lines.append("|------|------|----------|---------|")
        for e in demoted:
            lines.append(
                f"| {e['term']} | {e['freq_total']} "
                f"| {e['old_category']} "
                f"| {', '.join(e['reasons'][:3])} |"
            )
        lines.append("")

    # Privacy summary
    privacy_entries = [e for e in audit_entries
                       if e.get("old_category", "").startswith("privacy.")]
    if privacy_entries:
        lines.append("## Privacy Terms Audit")
        lines.append("")
        lines.append(f"- Total privacy terms: {len(privacy_entries)}")
        active_priv = sum(1 for e in privacy_entries if e["freq_total"] > 0)
        lines.append(f"- Terms with data occurrences: {active_priv}")
        lines.append(f"- Terms with zero occurrences: {len(privacy_entries) - active_priv}")
        lines.append("")

    # Chaos summary
    chaos_entries = [e for e in audit_entries
                     if e.get("old_category", "").startswith("chaos.")]
    if chaos_entries:
        lines.append("## Chaos Terms Audit")
        lines.append("")
        mild = [e for e in chaos_entries if "mild" in e.get("old_category", "")]
        strong = [e for e in chaos_entries if "strong" in e.get("old_category", "")]
        lines.append(f"- Mild chaos terms: {len(mild)}")
        lines.append(f"- Strong chaos terms: {len(strong)}")
        mild_active = sum(1 for e in mild if e["freq_total"] > 0)
        strong_active = sum(1 for e in strong if e["freq_total"] > 0)
        lines.append(f"- Mild terms with data: {mild_active}")
        lines.append(f"- Strong terms with data: {strong_active}")
        lines.append("")

    # Zero-frequency terms count
    zero_freq = [e for e in audit_entries if e["freq_total"] == 0]
    if zero_freq:
        lines.append(f"## Zero-Frequency Terms ({len(zero_freq)})")
        lines.append("")
        lines.append("These archived terms never appeared in the scanned data:")
        lines.append("")
        for e in zero_freq[:30]:
            lines.append(f"- `{e['term']}` ({e['old_category']})")
        if len(zero_freq) > 30:
            lines.append(f"- ... and {len(zero_freq) - 30} more")
        lines.append("")

    lines.append("## Next Steps")
    lines.append("")
    lines.append("1. Review **promoted** terms — these are verified high-value words for active lexicon.")
    lines.append("2. Review **demoted** terms — confirm low-frequency words should leave active lexicon.")
    lines.append("3. Review **privacy** terms — ensure sensitive terms are handled correctly.")
    lines.append("4. Review **chaos** terms — mild terms may re-enter active; strong should stay isolated.")
    lines.append("5. Run `generate_active_lexicon.py --audit <this_audit_file>` to regenerate lexicons.")
    lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")
