#!/usr/bin/env python3
"""
TOC creator (Flatpak) — mirrors the non-Flatpak behavior:

1) Prefer Python pdftotext with physical=True for layout fidelity.
   Fallbacks: Poppler CLI 'pdftotext -layout' → PyMuPDF.
2) Canonicalize text: normalize CRLF/CR and NBSPs to simple '\n' and ' '.
3) Load regex files (per-file '# flags: imsx' supported).
4) Build TOC with FOUR-DIGIT page numbers, like '0060', matching non-Flatpak.

Writes per-page text to `text_record_folder/NNNN.txt`.
Writes TOC text to `toc_file` and returns it.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import fitz  # PyMuPDF

PAGE_FILE_FMT = "{:04d}.txt"
PAGE_PAD = 4

# ---------- canonicalization ----------
def _canonicalize(text: str) -> str:
    # Normalize line-endings and NBSPs to match your non-Flatpak pipeline
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00A0", " ")

# ---------- regex loading ----------
_FLAG_MAP = {
    "i": re.IGNORECASE,
    "m": re.MULTILINE,
    "s": re.DOTALL,
    "x": re.VERBOSE,
}

def _parse_flag_line(line: str) -> Optional[int]:
    s = line.strip()
    if not s.lower().startswith("# flags:"):
        return None
    flags = 0
    tail = s.split(":", 1)[-1]
    for tok in tail.replace(",", " ").split():
        flags |= _FLAG_MAP.get(tok.strip().lower(), 0)
    return flags or None

def _category_files(regexes_folder: str) -> List[Path]:
    base = Path(regexes_folder)
    return [
        p for p in base.iterdir()
        if p.is_file() and not p.name.startswith(".") and p.name != "_order.txt"
    ]

def _load_category_file(path: Path) -> List[Tuple[str, int]]:
    items: List[Tuple[str, int]] = []
    flags = re.MULTILINE
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.rstrip("\n")
        maybe = _parse_flag_line(line)
        if maybe is not None:
            flags = maybe
            continue
        if not line.strip():
            continue
        if line.lstrip().startswith("#"):
            continue
        items.append((line, flags))
    return items

# ---------- utils ----------
def _ensure_dir(p: str | Path) -> None:
    Path(p).mkdir(parents=True, exist_ok=True)

def _iter_pdfs(folder: str) -> List[str]:
    return [
        str(Path(folder, name))
        for name in sorted(os.listdir(folder or ""))
        if name.lower().endswith(".pdf")
    ]

# ---------- extraction backends ----------
def _extract_pages_pdftotext_py(pdf_path: str, out_dir: Path) -> Optional[int]:
    """Return page count on success; None on failure."""
    try:
        import pdftotext  # C++ binding against Poppler
    except Exception:
        return None
    try:
        _ensure_dir(out_dir)
        with open(pdf_path, "rb") as f:
            pdf = pdftotext.PDF(f, physical=True)  # <-- key difference
            for i, page in enumerate(pdf, start=1):
                txt = _canonicalize(page)
                (out_dir / PAGE_FILE_FMT.format(i)).write_text(txt, encoding="utf-8")
        return len(pdf)
    except Exception:
        return None

def _extract_pages_pdftotext_cli(pdf_path: str, out_dir: Path, page_count_hint: Optional[int]) -> Optional[int]:
    exe = shutil.which("pdftotext")
    if not exe:
        return None
    try:
        _ensure_dir(out_dir)
        # If we don't know page count, get it via PyMuPDF quickly
        pages = page_count_hint
        if pages is None:
            try:
                with fitz.open(pdf_path) as d:
                    pages = d.page_count
            except Exception:
                pages = None

        if pages is None or pages <= 0:
            return None

        for i in range(1, pages + 1):
            out = out_dir / PAGE_FILE_FMT.format(i)
            cmd = [
                exe, "-layout",
                "-eol", "unix",
                "-enc", "UTF-8",
                "-f", str(i), "-l", str(i),
                pdf_path, str(out)
            ]
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            # Post-process to match canonicalization used by non-Flatpak
            try:
                txt = out.read_text(encoding="utf-8", errors="ignore")
                out.write_text(_canonicalize(txt), encoding="utf-8")
            except Exception:
                pass
        return pages
    except Exception:
        return None

def _extract_pages_fitz(pdf_path: str, out_dir: Path) -> int:
    _ensure_dir(out_dir)
    with fitz.open(pdf_path) as doc:
        try:
            flags = fitz.TEXT_PRESERVE_LIGATURES | fitz.TEXT_PRESERVE_WHITESPACE  # type: ignore[attr-defined]
        except Exception:
            flags = 0
        for i in range(doc.page_count):
            pg = doc.load_page(i)
            text = pg.get_text("text", flags=flags) if flags else pg.get_text("text")
            (out_dir / PAGE_FILE_FMT.format(i + 1)).write_text(
                _canonicalize(text), encoding="utf-8"
            )
        return doc.page_count

# ---------- main ----------
def create_toc(
    text_record_folder: str,
    input_folder: str,
    combined_pdf_path: str,
    toc_file: str,                 # REQUIRED: we write TOC text here
    regexes_folder: str,
    update_progress = None,
    *,
    debug_text: bool = False,
    max_debug_pages: Optional[int] = None,
) -> str:
    text_dir = Path(text_record_folder)
    _ensure_dir(text_dir)

    # 1) Merge PDFs → combined
    pdf_paths = _iter_pdfs(input_folder)
    if not pdf_paths:
        raise FileNotFoundError(f"No PDFs found in: {input_folder}")

    merged = fitz.open()
    for src_path in pdf_paths:
        with fitz.open(src_path) as src:
            merged.insert_pdf(src)
    merged.save(combined_pdf_path, deflate=True, garbage=3)
    total_pages = merged.page_count
    merged.close()

    # 2) Extract per-page text (prefer Python binding with physical=True)
    pages_done = _extract_pages_pdftotext_py(combined_pdf_path, text_dir)
    if pages_done is None:
        pages_done = _extract_pages_pdftotext_cli(combined_pdf_path, text_dir, total_pages)
    if pages_done is None:
        pages_done = _extract_pages_fitz(combined_pdf_path, text_dir)

    # 3) Load categories/patterns
    cat_files = _category_files(regexes_folder)
    if not cat_files:
        raise FileNotFoundError(f"No regex files found in: {regexes_folder}")

    categories: Dict[str, List[Tuple[str, int]]] = {}
    for p in cat_files:
        categories[p.stem] = _load_category_file(p)

    # 4) Read page texts
    page_files = [text_dir / PAGE_FILE_FMT.format(i) for i in range(1, pages_done + 1)]
    page_texts: List[str] = [pf.read_text(encoding="utf-8", errors="ignore") if pf.exists() else "" for pf in page_files]

    # 5) Scan pages
    from collections import defaultdict
    results: Dict[str, List[Tuple[str, int]]] = defaultdict(list)
    seen: Dict[str, set] = {cat: set() for cat in categories}

    def _run_pattern_on_text(pat_text: str, flags: int, text: str) -> List[str]:
        hits: List[str] = []
        try:
            pat = re.compile(pat_text, flags)
            for m in pat.finditer(text):
                # Use first capture if present, else the whole match
                s = (m.group(1) if m.lastindex else m.group(0)).strip()
                if s:
                    hits.append(s)
        except re.error:
            pass
        return hits

    for i, text in enumerate(page_texts, start=1):
        for cat, pat_items in categories.items():
            for pat_text, flags in pat_items:
                for s in _run_pattern_on_text(pat_text, flags, text):
                    key = (s, i)
                    if key not in seen[cat]:
                        results[cat].append((s, i))
                        seen[cat].add(key)
        if update_progress:
            update_progress(i / max(1, pages_done))

    # 6) Build TOC (pad page numbers to 4 digits)
    def _pad4(n: int) -> str: return f"{n:0{PAGE_PAD}d}"
    out_lines: List[str] = []
    for cat in sorted(categories.keys(), key=lambda s: s.lower()):  # match non-Flatpak ordering
        cat_hits = sorted(results.get(cat, []), key=lambda t: t[1])
        first_page = cat_hits[0][1] if cat_hits else 1
        out_lines.append(f"{cat} {_pad4(first_page)}")
        for title, pg in cat_hits:
            out_lines.append(f"\t{title} {_pad4(pg)}")
        out_lines.append("")

    toc_text = "\n".join(out_lines) + "\n"

    # 7) Write TOC file
    if toc_file:
        os.makedirs(os.path.dirname(toc_file), exist_ok=True)
        Path(toc_file).write_text(toc_text, encoding="utf-8")

    return toc_text

