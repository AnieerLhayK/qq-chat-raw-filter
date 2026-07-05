# Lexicon Archive

Archived at: 2026-07-05T15:12:40Z
Total archived entries: 654

## Purpose

This directory contains the **original hand-curated lexicons** archived
as historical reference. These files are **read-only** and do NOT
participate in active matching.

The active lexicons live in the parent directory (`lexicons/*.jsonl`).

## Archive vs Active

| Archive | Active |
|---------|--------|
| Historical snapshot | Data-verified working set |
| Read-only | Updated by audit + phrase mining |
| Full original词库 | Subset verified by frequency data |
| `participates_in_matching: false` | Used in pipeline scoring/matching |

## Workflow

1. Archive preserves the original hand-curated词库 (these files).
2. Pipeline runs include a frequency audit stage that checks each
   archived word against real data across all buckets.
3. `generate_active_lexicon.py` combines audit results with phrase
   mining discoveries to regenerate active lexicons.
4. Active lexicons get refined over time; archive stays unchanged.

## Files

| File | Entries | Description |
|------|---------|-------------|
| `chaos_lexicon_archive.jsonl` | 118 | Chaos / slang / profanity words — mild and strong severity |
| `drop_sentence_words_archive.jsonl` | 16 | Trigger words for dropping entire sentence blocks |
| `manual_drop_archive.jsonl` | 0 | Manually specified phrases to always drop |
| `manual_keep_archive.jsonl` | 0 | Manually specified phrases to always keep |
| `mask_words_archive.jsonl` | 29 | Words to mask/replace in output blocks |
| `phrase_bank_archive.jsonl` | 349 | Style markers — analysis, tone, interruption, argument, tag markers |
| `phrase_candidates_archive.jsonl` | 0 | Auto-discovered phrase candidates from phrase mining |
| `phrase_stoplist_archive.txt` | 62 | Stoplist — high-frequency low-value phrases to filter out |
| `privacy_lexicon_archive.jsonl` | 80 | Privacy risk words — high and medium risk levels |

## Schema

Every archived JSONL entry has these additional fields:

- `old_category`: original classification (e.g. `style_markers.analysis_marker`)
- `archived_at`: ISO-8601 timestamp of archive creation
- `participates_in_matching`: always `false` for archive entries
- `source_file`: original filename this entry came from

Original fields (`phrase`, `label`, `source`, `status`, `severity`, `risk`, etc.)
are preserved exactly as they were at migration time.
