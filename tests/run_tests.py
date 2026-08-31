#!/usr/bin/env python3
"""Test runner for automation-racehorse.

Usage (from the repo root, or anywhere):
    python3 tests/run_tests.py

Discovers every test_*.py under this directory and runs it with unittest's
TextTestRunner, exiting non-zero on any failure or error so it composes
cleanly with CI / pre-commit hooks.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Make sure `racehorse_lib` is importable regardless of the caller's cwd:
# insert the repo root (this file's parent's parent) at the front of
# sys.path before discovery runs.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    loader = unittest.defaultTestLoader
    suite = loader.discover(start_dir=str(Path(__file__).resolve().parent), top_level_dir=str(REPO_ROOT))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
