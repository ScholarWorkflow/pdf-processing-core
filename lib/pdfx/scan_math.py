"""Ambiguous plain-text math scanner with region-map anchoring.

Formula-region scanning stage. Consumes the
per-PDF sidecar produced by formula_regions.py and turns llm-ocr-refresh's
old prose regex criteria into a script verdict with three jobs:

  1. DOUBLE-INSURANCE SCAN - regex over the whole md body (LaTeX-delimited
     segments are masked first, so well-formed $...$ never fires). Region
     anchoring ADDS precision; it never narrows the scan.
   2. PAGE ATTRIBUTION - each hit is mapped to a physical page of the paired
      PDF via explicit page markers or a configured ordinal section header.
  3. ANCHORING -> REPAIR UNIT - locate the hit on the page geometry
     (PyMuPDF search_for), intersect with the sidecar regions, and emit the
     minimal repair unit:
       region - hit falls inside a whitelisted region (crop-repairable);
       page   - no anchor or no covering region (fall back to whole page).

Sidecar consumption rule: on pages
where layout-source regions exist, span-source regions are IGNORED for
anchoring - garbled-layer span classes are unreliable; layout boxes win.

Output: fixed-schema JSON (hits[] + stats) consumed by llm-ocr-refresh;
never mutates anything.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from pathlib import Path

from .formula_regions import load_sidecar, regions_for_page

logger = logging.getLogger(__name__)

# --- masking: well-formed LaTeX never triggers -------------------------------

RE_LATEX_SEGMENT = re.compile(
    r"\$\$.*?(?:\$\$|$)"        # $$...$$ (or unterminated opener till EOL)
    r"|\$[^$\n]+\$"             # $...$
    r"|\\\([^\n]*?\\\)"         # \( ... \)
    r"|\\\[[^\n]*?\\\]"         # \[ ... \]
)

def mask_latex(line: str) -> str:
    """Blank out LaTeX-delimited spans, preserving column offsets."""
    return RE_LATEX_SEGMENT.sub(lambda m: " " * len(m.group()), line)

# --- ambiguity patterns (ported verbatim from llm-ocr-refresh criteria) ------

PATTERNS = [
    ("exp_chain", re.compile(r"[0-9A-Za-z\)\]]\^\s?[^\s{}$]+\^\s?[^\s{}$]+")),     # e^x^2, a^b^c
    ("subsup_stack", re.compile(                                                    # x_n^2, x^n_m
        r"[0-9A-Za-z\)]_[^\s{}$]*\^[^\s{}$]+|[0-9A-Za-z\)]\^[^\s{}$]+_[^\s{}$]")),
    ("slash_frac_mul", re.compile(                                                  # 1/2x, a/x·y
        r"(?:\d+|[A-Za-z])\s*/\s*(?:\d+|[A-Za-z])\s*[·×⋅]?\s*(?:\d|[A-Za-z])(?![A-Za-z])")),
    ("fn_adjacent", re.compile(                                                     # sin x+1, log 2x
        r"\b(?:sin|cos|tan|cot|sec|csc|log|ln|exp|lim)\s+(?=[0-9A-Za-z(])", re.I)),
    ("sqrt_adjacent", re.compile(r"√\s*[^\s+]")),                                   # √x+1
]

# --- md page markers ----------------------------------------------------------

RE_PAGE_MARKER = re.compile(r"<!--\s*PDF_PAGE:\s*(\d+)\s*-->")
PAGE_HEADER_PREFIX = os.environ.get("PDFX_PAGE_HEADER_PREFIX", "原书").strip() or "原书"
RE_SECTION_HEADER = re.compile(
    rf"^#{{2,4}}\s*{re.escape(PAGE_HEADER_PREFIX)}\s+", re.M | re.IGNORECASE
)


def parse_md_pages(md_path: str | Path):
    """md text -> (lines_with_page, mode).

    lines_with_page: [(line_no_1based, text, phys_page_or_None)].
    explicit marker: `<!-- PDF_PAGE: N -->`; ordinal mode: the k-th configured
    section header equals the k-th page of the derived PDF; neither
    -> everything on page None (caller treats as unknown/unpaged).
    """
    raw = Path(md_path).read_text(encoding="utf-8")
    lines = raw.splitlines()
    # skip YAML frontmatter
    start = 1 if lines and lines[0].strip() == "---" else 0
    if start:
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                start = i + 1
                break
    has_explicit = any(RE_PAGE_MARKER.search(ln) for ln in lines[start:])
    has_ordinal = bool(RE_SECTION_HEADER.search("\n".join(lines[start:])))
    out = []
    page = None
    ordinal = 0
    for idx in range(start, len(lines)):
        ln = lines[idx]
        if has_explicit:
            m = RE_PAGE_MARKER.search(ln)
            if m:
                page = int(m.group(1))
        elif has_ordinal:
            if RE_SECTION_HEADER.match(ln):
                ordinal += 1
                page = ordinal
        out.append((idx + 1, ln, page))
    mode = "explicit" if has_explicit else ("ordinal" if has_ordinal else "none")
    return out, mode


# --- anchoring -----------------------------------------------------------------

def _anchor_on_page(page, hit_text: str) -> list:
    """Search hit text on the rendered page geometry -> [Rect, ...] (pt).

    Whitespace-tolerant: tries progressively shorter normalized snippets.
    """
    import pymupdf as fitz

    snippets = []
    flat = re.sub(r"\s+", " ", hit_text.strip())
    if flat:
        snippets.append(flat[:60])
        tokens = flat.split()
        if len(tokens) >= 3:
            snippets.append(" ".join(tokens[:6]))
        core = re.sub(r"[^\w.\-+/^_=√Σ∫]", "", flat)[:24]
        if core:
            snippets.append(core)
    for snip in snippets:
        try:
            rects = page.search_for(snip)
        except Exception:  # noqa: BLE001 - malformed glyphs etc.
            rects = []
        if rects:
            return [fitz.Rect(r) for r in rects]
    return []


def _smallest_covering_region(hit_rect, regions, min_overlap=0.3):
    """Smallest sidecar region covering >=min_overlap of the hit rect."""
    import pymupdf as fitz

    harea = max(1e-6, hit_rect.get_area())
    best = None
    for reg in regions:
        rr = fitz.Rect(reg["bbox_pt"])
        inter = (hit_rect & rr).get_area() if hit_rect.intersects(rr) else 0.0
        if inter / harea >= min_overlap:
            if best is None or rr.get_area() < fitz.Rect(best["bbox_pt"]).get_area():
                best = reg
    return best


def _regions_for_anchoring(side: dict | None, phys_page: int) -> tuple[list, str]:
    """Regions usable for anchoring on one page + which source ruled.

    known_limits #3: layout source wins when present on the page.
    """
    regs = regions_for_page(side, phys_page)
    layout = [r for r in regs if r.get("source") == "layout"]
    if layout:
        return layout, "layout"
    return [r for r in regs if r.get("source") == "span"], "span"


# --- main entry -----------------------------------------------------------------

def scan_md(md_path: str | Path, pdf_path: str | None = None,
            dpi_hint: int = 150) -> dict:
    """Scan one text/md for ambiguous plain-text math; anchor hits against
    the sidecar region map when a paired PDF is given. Read-only."""
    lines, mode = parse_md_pages(md_path)
    side = load_sidecar(pdf_path) if pdf_path else None

    doc = None
    if pdf_path:
        import pymupdf as fitz
        doc = fitz.open(pdf_path)

    hits = []
    n_anchored = n_region = 0
    for line_no, text, phys in lines:
        if not text.strip() or text.lstrip().startswith(("<!--", "#")):
            continue                       # 标记行/标题行不参与
        masked = mask_latex(text)
        if masked == text and "$" in text:
            continue                       # 整行都在定界段里
        found = []
        for name, rex in PATTERNS:
            m = rex.search(masked)
            if m:
                found.append(name)
        if not found:
            continue

        entry = {
            "line_no": line_no,
            "phys_page": phys,
            "text": text.strip()[:120],
            "patterns": found,
            "anchor": None,
            "region": None,
            "repair_unit": "page",
        }
        if doc is not None and phys is not None and 1 <= phys <= len(doc):
            rects = _anchor_on_page(doc[phys - 1], text)
            if rects:
                regs, src = _regions_for_anchoring(side, phys)
                hit_union = rects[0]
                for r in rects[1:]:
                    hit_union |= r
                reg = _smallest_covering_region(hit_union, regs)
                entry["anchor"] = {"bbox_pt": [round(v, 1) for v in hit_union],
                                   "matched_by": "search"}
                n_anchored += 1
                if reg is not None:
                    entry["region"] = {"bbox_pt": reg["bbox_pt"],
                                       "class": reg["class"],
                                       "source": reg.get("source")}
                    entry["repair_unit"] = "region"
                    n_region += 1
        hits.append(entry)

    if doc is not None:
        doc.close()

    return {
        "md": str(md_path),
        "pdf": str(pdf_path) if pdf_path else None,
        "marker_mode": mode,
        "sidecar_found": side is not None,
        "hits": hits,
        "stats": {
            "lines_scanned": len(lines),
            "hits": len(hits),
            "anchored": n_anchored,
            "region_units": n_region,
            "page_units": sum(1 for h in hits if h["repair_unit"] == "page"),
        },
    }


def main(argv: list[str]) -> int:       # pragma: no cover - thin CLI
    import argparse
    ap = argparse.ArgumentParser(prog="scan-math")
    ap.add_argument("md")
    ap.add_argument("--pdf", default=None, help="paired PDF for anchoring (sidecar loaded from same stem)")
    args = ap.parse_args(argv)
    print(json.dumps(scan_md(args.md, args.pdf), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
