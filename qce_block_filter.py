#!/usr/bin/env python3
"""
QQ Chat Raw Material Filter — CLI entry point.

Thin wrapper that delegates to the qq_raw_filter package.
All pipeline logic lives in qq_raw_filter/ — this file is the public API.

Usage:
  python qce_block_filter.py --me-id 123456789
  python qce_block_filter.py --help
"""
import sys
from qq_raw_filter._cli import main

if __name__ == "__main__":
    sys.exit(main())
