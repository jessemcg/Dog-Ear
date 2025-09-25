#!/usr/bin/env python3
"""
00-dedup-exact.py
Remove exact duplicate non-empty lines from toc.txt (keep first; preserve order).
Empty lines are kept and not deduplicated.
"""

from __future__ import annotations
import os
from pathlib import Path
import sys

def main() -> None:
    toc_path = Path(os.environ.get("PDFMARKER_TOC", "toc.txt"))
    if not toc_path.exists():
        print(f"Error: {toc_path} does not exist.", file=sys.stderr)
        sys.exit(1)

    original = toc_path.read_text(encoding="utf-8", errors="replace")
    had_trailing_nl = original.endswith("\n")
    lines = original.splitlines()

    seen: set[str] = set()
    out: list[str] = []
    removed = 0

    for ln in lines:
        if not ln.strip():   # keep all empty lines
            out.append(ln)
            continue
        if ln not in seen:
            seen.add(ln)
            out.append(ln)
        else:
            removed += 1

    toc_path.write_text("\n".join(out) + ("\n" if had_trailing_nl else ""), encoding="utf-8")
    print(f"Removed {removed} exact duplicate non-empty line(s). Updated: {toc_path}")

if __name__ == "__main__":
    main()

