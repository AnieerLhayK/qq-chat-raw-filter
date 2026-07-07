"""
QQ Chat Raw Material Filter — qq_raw_filter.

A pipeline that processes QQ Chat Exporter (QCE) V5 exports into structured,
bucketed raw material for subsequent character skill extraction.

Key components:
  - qce_parser: QCE JSON parsing & identity filtering
  - block_builder: message → turn → session → MyBlock pipeline
  - scorer: style / privacy / junk / chaos scoring
  - bucket: bucket classification per block
  - pipeline: full pipeline orchestration
  - lexicon_auditor: frequency audit of archived lexicon words
  - phrase_miner: automatic n-gram discovery (2-4 chars)
  - generate_active_lexicon: data-driven active lexicon regeneration

Typical usage:
  from qq_raw_filter.pipeline import run_pipeline
  from qq_raw_filter.config_loader import load_config
"""

from qq_raw_filter.block_builder import MyBlock
from qq_raw_filter.qce_parser import ParsedMessage

__all__ = ["MyBlock", "ParsedMessage"]
