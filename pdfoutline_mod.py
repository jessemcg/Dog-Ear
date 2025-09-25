#!/usr/bin/env python3
"""
pdfoutline_mod.py — write PDF bookmarks (outline) using PyMuPDF.

Exports a single function:

    pdfoutline(inpdf, outpdf, tocfile=None, toc_text=None,
               update_progress=None, offset=0)

- `toc_text` is preferred (a string with lines "title<space>page", tabs for depth).
- If `toc_text` is None, `tocfile` is read from disk.
- `offset` is added to every page number (useful if your page counting differs).
- `update_progress` may be a callable(float) that receives progress in [0,1].

PyMuPDF (pymupdf) must be installed.
"""
from __future__ import annotations

import re
from typing import List, Optional, Callable, Tuple

import fitz  # PyMuPDF


def _parse_toc_lines(toc_text: str, offset: int = 0) -> List[Tuple[int, str, int]]:
    """Parse TOC lines into a list of (level, title, page).
    Level is 1-based (PyMuPDF expects 1 for top level).
    Each non-empty line is:
        \t*TITLE<space>PAGE
    where leading tabs define the depth.
    """
    out: List[Tuple[int, str, int]] = []
    for raw in toc_text.splitlines():
        if not raw.strip():
            continue
        # count leading tabs
        i = 0
        while i < len(raw) and raw[i] == '\t':
            i += 1
        level = i + 1  # PyMuPDF expects 1-based levels
        line = raw[i:].rstrip()
        m = re.match(r"^(.*)\s+(\d+)$", line)
        if not m:
            # Skip malformed lines rather than raising; keeps app resilient
            continue
        title = m.group(1).strip()
        page = int(m.group(2)) + offset
        if page < 1:
            page = 1
        out.append((level, title, page))
    return out


def _entries_to_pymupdf_toc(entries: List[Tuple[int, str, int]]) -> List[List[object]]:
    """Convert to the structure required by PyMuPDF: [[level, title, page, ...], ...]."""
    return [[level, title, page] for (level, title, page) in entries]


def pdfoutline(
    *,
    inpdf: str,
    outpdf: str,
    tocfile: Optional[str] = None,
    toc_text: Optional[str] = None,
    update_progress: Optional[Callable[[float], None]] = None,
    offset: int = 0,
) -> None:
    """Write bookmarks to `inpdf` and save as `outpdf` based on the provided TOC."""
    if update_progress:
        update_progress(0.05)

    if toc_text is None:
        if not tocfile:
            raise ValueError("Either toc_text or tocfile must be provided")
        with open(tocfile, "r", encoding="utf-8") as f:
            toc_text = f.read()

    if update_progress:
        update_progress(0.10)

    entries = _parse_toc_lines(toc_text, offset=offset)
    toc_list = _entries_to_pymupdf_toc(entries)

    if update_progress:
        update_progress(0.20)

    doc = fitz.open(inpdf)
    try:
        doc.set_toc(toc_list)
        # garbage=3 does xref cleanup; deflate compresses streams when possible
        doc.save(outpdf, deflate=True, garbage=3)
    finally:
        doc.close()

    if update_progress:
        update_progress(1.0)

