"""Formula/table region mapping persisted as a per-PDF sidecar JSON.

Why: math repair needs to know WHERE formulas and tables sit on a page.
Char-quality tiers (quality.py) say whether the text layer is clean; this
module maps WHERE non-prose content lives, so downstream consumers
(math anchoring, region-granular repair, and visual verification) can anchor
on coordinates instead of guessing from
regex hits alone.

Two acquisition paths:
  A span   - derive candidate regions from the text layer's own line
             geometry (zero model cost). Covers digital pages. Runs eager
             via the texlayer_audit hook.
  B layout - element boxes through the configured layout provider.
             Only worth calling on pages whose char tier is untrusted/empty
              (image-heavy pages); runs lazy at repair time via
              `cli.py regions <pdf> --layout`. When the layout provider is
             down this degrades silently per page (skipped map).

Sidecar contract: written NEXT TO THE PDF as `<pdf stem>.regions.json`,
coordinates in PDF points (pt); render-time zoom converts pt to pixels.
Schema:
  {
    "fingerprint": "<name>:<size>:<mtime>:<dpi>",
    "dpi": 150,
    "generated_at": "...",
    "known_limits": [...],
    "pages": {"12": [{"bbox_pt": [x0,y0,x1,y1], "text": "...",
                      "class": "...", "source": "span|layout", ...}]},
    "skipped": {"13": "layout_unavailable"},
    "summary": {...}
  }

Known blind spot (recorded in known_limits): flattened-but-legal corruption
(e.g. x_n^m extracted as x_nm) carries no signal for either char stats or
ambiguity regexes; table-structure validation is future work.

Label calibration uses a small vocabulary of layout-provider labels. Aliases
are accepted so provider vocabulary drift degrades to "ignored label" (logged),
never a crash.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from .quality import cjk_skeleton

logger = logging.getLogger(__name__)

# --- path A: span-geometry derivation --------------------------------------

HEADER_BAND = 0.06           # line centre-y above/below these page fractions -> running head/footer
FOOTER_BAND = 0.06
PROSE_MIN_SKELETON = 4       # parity with texlayer_audit v2 prose gate
PROSE_CJK_RATIO = 0.5
LINE_GAP_FACTOR = 1.5        # parity with texlayer_audit v2 clustering params
X_OVERLAP_FRAC = 0.3

_MATH_CHARS = (
    "=^_"
    "\u2211\u222b\u222e\u221a"          # ∑ ∫ ∮ √
    "\u00b1\u2213\u00d7\u00f7"          # ± ∓ × ÷
    "\u2264\u2265\u2260\u2248\u2261"    # ≤ ≥ ≠ ≈ ≡
    "\u2202\u2207\u221e"                # ∂ ∇ ∞
    "\u2208\u2209\u2282\u2283\u2286\u2287\u222a\u2229"  # ∈ ∉ ⊂ ⊃ ⊆ ⊇ ∪ ∩
    "\u2200\u2203\u2234"                # ∀ ∃ ∴
    "\u2190\u2191\u2192\u2193\u21d2\u21d4"  # arrows
    "\u0391-\u03a9\u03b1-\u03c9"        # Greek
)
RE_MATH_CHAR = re.compile(f"[{_MATH_CHARS}]")
RE_LATEX_CMD = re.compile(r"\\[a-zA-Z]+")


def _has_math_evidence(text: str) -> bool:
    return bool(RE_MATH_CHAR.search(text) or RE_LATEX_CMD.search(text))


def _candidate_lines(page) -> list:
    """Non-prose text lines with bboxes; header/footer bands excluded."""
    import pymupdf as fitz

    ph = page.rect.height
    lo, hi = ph * HEADER_BAND, ph * (1 - FOOTER_BAND)
    out = []
    for blk in page.get_text("dict").get("blocks", []):
        if blk.get("type") != 0:
            continue
        for ln in blk.get("lines", []):
            txt = "".join(s.get("text", "") for s in ln.get("spans", [])).strip()
            if not txt:
                continue
            r = fitz.Rect(ln["bbox"])
            cy = (r.y0 + r.y1) / 2
            if cy < lo or cy > hi:
                continue
            skel_len = len(cjk_skeleton(txt))
            if skel_len >= PROSE_MIN_SKELETON and \
                    skel_len / max(1, len(txt)) >= PROSE_CJK_RATIO:
                continue                      # prose line -> not a region
            out.append((r, txt))
    return out


def _cluster_lines(items) -> list:
    """Merge nearby non-prose line rects into region clusters (same merge
    parameters as the texlayer_audit v2 paragraph clustering)."""
    import pymupdf as fitz

    ordered = sorted(items, key=lambda t: (t[0].y0, t[0].x0))
    clusters: list[dict] = []
    for rect, txt in ordered:
        best, best_gap = None, None
        for c in clusters:
            lr = c["rects"][-1]
            x_ov = min(rect.x1, lr.x1) - max(rect.x0, lr.x0)
            if x_ov < min(lr.width, rect.width) * X_OVERLAP_FRAC:
                continue
            gap = rect.y0 - lr.y1
            if gap > LINE_GAP_FACTOR * max(lr.height, rect.height):
                continue
            if best is None or abs(gap) < abs(best_gap):
                best, best_gap = c, gap
        if best is None:
            clusters.append({"rects": [rect], "texts": [txt]})
        else:
            best["rects"].append(rect)
            best["texts"].append(txt)
    out = []
    for c in clusters:
        u = c["rects"][0]
        for r in c["rects"][1:]:
            u |= r
        if u.is_empty:
            continue
        cls = "formula" if any(_has_math_evidence(t) for t in c["texts"]) \
            else "suspect_nonprose"
        out.append({"bbox_pt": [round(v, 1) for v in u],
                    "text": " / ".join(t.strip() for t in c["texts"])[:120],
                    "class": cls,
                    "source": "span"})
    return out


def span_regions(page) -> list:
    """Path A: one page's text-layer-derived regions (pt coords)."""
    return _cluster_lines(_candidate_lines(page))


# --- path B: pp-doclayout element boxes -------------------------------------

# deployed vocabulary plus aliases for other PP-DocLayout releases;
# unknown labels are ignored (counted in debug logs), never fatal.
LAYOUT_LABEL_MAP = {
    "display_formula": "isolated_formula",   # observed deployment alias
    "interline_formula": "isolated_formula",
    "isolated_formula": "isolated_formula",
    "formula": "isolated_formula",
    "equation": "isolated_formula",
    "inline_formula": "formula",
    "table": "table",
}
FORMULA_NUMBER_LABEL = "formula_number"      # "(1.2)" tag merged into its display formula


def layout_regions(page, dpi: int) -> tuple[list, str | None]:
    """Path B: one page's pp-doclayout regions -> (regions, error).

    error is None on success, else 'layout_unavailable'. Boxes come back in
    rendered-pixel coordinates of the posted PNG; converted to pt here.
    """
    from . import toc_layout
    from ._vlhttp import render_page_png

    try:
        png = render_page_png(page, dpi=dpi)
        lay = toc_layout.layout_boxes(png)
    except Exception as e:  # noqa: BLE001 - endpoint down / cold-start fail
        logger.warning("layout path failed p%s: %s", page.number + 1, e)
        return [], "layout_unavailable"

    w = lay.get("width") or 1
    h = lay.get("height") or 1
    sx = page.rect.width / w     # px -> pt straight off the page rect
    sy = page.rect.height / h
    raw = lay.get("boxes") or []
    regions: list[dict] = []
    numbers: list[dict] = []
    for bx in raw:
        label = str(bx.get("label", "")).lower()
        bb = bx.get("bbox")
        if len(bb) != 4:
            continue
        if label == FORMULA_NUMBER_LABEL:
            numbers.append({"bbox_pt": [round(bb[0] * sx, 1), round(bb[1] * sy, 1),
                                        round(bb[2] * sx, 1), round(bb[3] * sy, 1)],
                            "label": label})
            continue
        cls = LAYOUT_LABEL_MAP.get(label)
        if cls is None:
            logger.debug("layout label %r not whitelisted (p%s)", label,
                         page.number + 1)
            continue
        regions.append({"bbox_pt": [round(bb[0] * sx, 1), round(bb[1] * sy, 1),
                                    round(bb[2] * sx, 1), round(bb[3] * sy, 1)],
                        "class": cls,
                        "source": "layout",
                        "label": label})

    import pymupdf as fitz

    for num in numbers:          # 编号框并入纵向交叠的显示公式右缘（裁剪时带着 (1.2) 号看）
        nb = fitz.Rect(num["bbox_pt"])
        for reg in regions:
            if reg["class"] != "isolated_formula":
                continue
            rb = fitz.Rect(reg["bbox_pt"])
            if rb.y0 <= nb.y1 and nb.y0 <= rb.y1:
                rb.x1 = max(rb.x1, nb.x1)
                rb.y0 = min(rb.y0, nb.y0)
                rb.y1 = max(rb.y1, nb.y1)
                reg["bbox_pt"] = [round(v, 1) for v in rb]
                break
    regions.sort(key=lambda e: (e["bbox_pt"][1], e["bbox_pt"][0]))
    return regions, None


# --- sidecar I/O -------------------------------------------------------------

KNOWN_LIMITS = [
    "压扁公式不可见：x_n^m 被提取成 x_nm 这类『合法但义错』的文本，字符统计与歧义正则都无信号（表结构化校验待做）",
    "Span geometry covers text-bearing pages; image-only pages require a configured layout provider and may be skipped when unavailable",
    "乱码页(untrusted)上 span 分类可能误报：坏字符合法性失真把标题/正文挤成候选区，且乱码字形可能碰巧命中数学特征正则",
    "synthetic garbled-layer fixture: a title may be misclassified as formula while layout correctly returns no formula regions"
    "此类页消费时以 layout 源为准——layout 有区的页忽略 span 的 class，layout 无区时该页区域仅作几何参考",
]

BAD_PAGE_TIERS = {"untrusted", "empty"}   # path B only pays GPU on these


def fingerprint(pdf_path: str, dpi: int) -> str:
    p = Path(pdf_path)
    return f"{p.name}:{p.stat().st_size}:{int(p.stat().st_mtime)}:{dpi}"


def sidecar_path(pdf_path: str) -> Path:
    return Path(pdf_path).with_suffix(".regions.json")


def load_sidecar(pdf_path: str) -> dict | None:
    sp = sidecar_path(pdf_path)
    if not sp.is_file():
        return None
    try:
        return json.loads(sp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def write_sidecar(pdf_path: str, data: dict) -> Path:
    sp = sidecar_path(pdf_path)
    sp.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                  encoding="utf-8")
    return sp


def regions_for_page(data: dict | None, page_no: int) -> list:
    if not data:
        return []
    return (data.get("pages") or {}).get(str(page_no)) or []


def parse_page_spec(raw: str | None) -> list[int] | None:
    """'3,7-9' -> [3,7,8,9]; None/empty -> None (= all pages)."""
    if not raw:
        return None
    out: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return sorted(out)


def build_regions(pdf_path: str, pages=None, use_layout: bool = False,
                  dpi: int = 150, progress=None) -> dict:
    """Compute/refresh the region sidecar.

    pages: iterable of physical page numbers (None = all). Merges into an
    existing sidecar when the fingerprint matches; a stale fingerprint
    starts fresh. use_layout adds path B on untrusted/empty pages.
    """
    import pymupdf as fitz

    from .quality import scan_pdf

    fp = fingerprint(pdf_path, dpi)
    old = load_sidecar(pdf_path)
    data: dict = {"fingerprint": fp, "dpi": dpi,
                  "generated_at": datetime.now().isoformat(timespec="seconds"),
                  "known_limits": list(KNOWN_LIMITS),
                  "pages": {}, "skipped": {}}
    if old and old.get("fingerprint") == fp:
        data["pages"] = dict(old.get("pages") or {})
        data["skipped"] = dict(old.get("skipped") or {})

    doc = fitz.open(pdf_path)
    total = len(doc)
    want = sorted(set(pages)) if pages else range(1, total + 1)
    tiers = {q.page: q.tier for q in scan_pdf(pdf_path)} if use_layout else {}

    say = progress or (lambda s: None)
    n_span = n_layout = 0
    for pno in want:
        if not (1 <= pno <= total):
            continue
        page = doc[pno - 1]
        regs = span_regions(page)
        skipped_here = None
        if use_layout and tiers.get(pno) in BAD_PAGE_TIERS:
            lregs, err = layout_regions(page, dpi)
            if err:
                skipped_here = err
            else:
                regs.extend(lregs)
                regs.sort(key=lambda e: (e["bbox_pt"][1], e["bbox_pt"][0]))
        data["pages"][str(pno)] = regs
        data["skipped"].pop(str(pno), None)
        if skipped_here:
            data["skipped"][str(pno)] = skipped_here
        s = sum(1 for r in regs if r["source"] == "span")
        l = sum(1 for r in regs if r["source"] == "layout")
        n_span += s
        n_layout += l
        say(f"formula-regions: p{pno} span={s} layout={l}"
            + (f" skipped={skipped_here}" if skipped_here else ""))
    doc.close()
    data["summary"] = {"pages_mapped": len(data["pages"]),
                       "regions_span_this_run": n_span,
                       "regions_layout_this_run": n_layout,
                       "skipped_pages": len(data["skipped"])}
    return data
