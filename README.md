# QQ Chat Raw Material Filter

[简体中文](README.zh-CN.md)

Local-first tooling for transforming QQ Chat Exporter v5 JSON exports into privacy-aware, reviewable material for writing and character-skill workflows. It reads exports without modifying them and produces structured JSONL buckets, audit data, phrase candidates, and tuning evidence.

## Maintenance model

Keep raw exports, generated data, lexicon experiments, and personal configuration outside version control. Preserve the local-first and privacy-first design: do not upload chat data or commit private samples.

## Pipeline

```text
QCE v5 JSON export
  -> parse and identify speakers
  -> merge messages into turns, sessions, and blocks
  -> score style, privacy, junk, and chaos signals
  -> bucket results, audit lexicons, and optionally mine phrases
```

Buckets include `candidates`, `micro_style`, `need_anonymize`, `chaos_style`, `debatable`, and `rejected`.

## Quick start

```bash
pip install -e .
python qce_block_filter.py --me-id 123456789
python qce_block_filter.py --me-id 123456789 --debug --limit-files 2
```

Use `--input-dir` and `--output-dir` to override local paths. `--dry-run` parses without writing output; `--mine-phrases` enables phrase discovery. Run `python qce_block_filter.py --help` for all options.

## Privacy, tests, and related projects

Processing is local and raw exports are read-only. Material requiring anonymization remains separate from ordinary candidates, and privacy-related phrases do not automatically enter an allowed output lexicon.

```bash
pytest tests/ -v
```

Python 3.11+ uses the standard library; Python 3.8–3.10 also needs `tomli`.

- [Frame for AI Workspace](https://github.com/AnieerLhayK/Frame-for-AI-workspace)
- [Chatty Ch System](https://github.com/AnieerLhayK/Chatty-Ch-System)
- [QQ Chat Exporter](https://github.com/shuakami/qq-chat-exporter)
