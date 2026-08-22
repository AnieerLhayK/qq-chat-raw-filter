#!/usr/bin/env python3
"""Validate a public qq-chat-raw-filter projection."""
from __future__ import annotations

import argparse
import re
from pathlib import Path

REQUIRED_PATHS = {'qq_raw_filter/__init__.py', '.github/workflows/ci.yml', 'qce_block_filter.py', 'PROJECTION_SOURCE.json', 'README.md', 'tests/test_public_smoke.py', 'pyproject.toml'}
FORBIDDEN_PARTS = {'.pytest_cache', 'output', '.mypy_cache', '.ruff_cache', '__pycache__', 'corpus'}
FORBIDDEN_PATTERNS = ['D:[\\\\/]+AI', 'C:[\\\\/]+Users']
FORBIDDEN_PATTERNS = [re.compile(pattern, re.IGNORECASE) for pattern in FORBIDDEN_PATTERNS]
TEXT_SUFFIXES = {'.yaml', '.json', '.toml', '.txt', '.gitignore', '.py', '.yml', '.md'}

def is_text(path: Path) -> bool:
    return path.name == ".gitignore" or path.suffix.lower() in TEXT_SUFFIXES

def check_required(root: Path) -> list[str]:
    return [
        f"Missing required path: {rel}"
        for rel in sorted(REQUIRED_PATHS)
        if not (root / rel).is_file()
    ]

def check_forbidden_paths(root: Path) -> list[str]:
    issues = []
    for path in root.rglob("*"):
        if not path.is_dir():
            continue
        rel = path.relative_to(root).as_posix()
        parts = path.relative_to(root).parts
        if any(part in FORBIDDEN_PARTS for part in parts):
            issues.append(f"Forbidden path exists: {rel}")
        if any(part.endswith(".egg-info") for part in parts):
            issues.append(f"Forbidden build metadata exists: {rel}")
    return sorted(set(issues))

def check_text(root: Path) -> list[str]:
    issues = []
    for path in root.rglob("*"):
        if not path.is_file() or ".git" in path.parts or not is_text(path):
            continue
        rel = path.relative_to(root).as_posix()
        if rel == "scripts/check_public_package.py":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in FORBIDDEN_PATTERNS:
            match = pattern.search(text)
            if match:
                line = text[: match.start()].count("\n") + 1
                issues.append(f"{rel}:{line}: forbidden text matched {pattern.pattern!r}")
    return issues

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default=".")
    root = Path(parser.parse_args().dir).resolve()
    issues = check_required(root) + check_forbidden_paths(root) + check_text(root)
    if issues:
        print("FAILED: public qq-chat-raw-filter boundary check found issues:")
        for issue in issues:
            print(f"  - {issue}")
        return 1
    print("PASSED: public qq-chat-raw-filter boundary check.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
