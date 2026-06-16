#!/usr/bin/env python3
"""Validate changelog fragments added in a PR. Used by changelog-check.yml.

Usage: check_fragments.py <file> [<file> ...]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from changelog_lib import FragmentError, parse_fragment  # noqa: E402


def main(argv):
    if not argv:
        print("::error::No changelog fragment files were given to validate")
        return 1

    failed = False
    for raw_path in argv:
        path = Path(raw_path)
        try:
            fragment = parse_fragment(path)
        except FragmentError as exc:
            print(f"::error::{exc}")
            failed = True
            continue
        print(f"OK: {path} (bump={fragment['bump']}, type={fragment['type']})")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
