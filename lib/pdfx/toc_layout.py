"""TOC structure recovery via a layout provider and projection analysis.

Why: PP-DocLayoutV2 is BLOCK-level - a dense TOC page comes back as one
giant "content" box, so its boxes alone cannot reveal per-entry indentation.
The hierarchy evidence lives in the book's own typography: each entry line's
LEFT INK EDGE. This module therefore combines

  1. a configured stage-1 layout provider (multipart field ``file``) to bound
     the content region and drop headers/footers/page-numbers;
  2. classic projection analysis INSIDE that region: row ink profile splits
     lines, each line's leftmost ink column is its indent;
  3. per-line strip OCR reads title + dot leaders + trailing
     page number together - rescuing pages whose whole-page transcription
     lost the number column.

Levels come from clustering the normalized left edges ACROSS all TOC pages
(the book's own indentation scale). If a book indents nothing (single
cluster) callers fall back to numbering semantics - documented, not silent.
"""

from __future__ import annotations

import bisect
import logging
import re
import time

from ._vlhttp import post_image, post_multipart, provider_model, render_page_png

logger = logging.getLogger(__name__)

LAYOUT_ROLE = "layout"
CONTENT_LABELS = {"content", "text", "paragraph_title", "title", "doc_title"}
EDGE_LABELS = {"header", "footer", "number", "footnote"}
LINE_MERGE_GAP_PX = 3           # bands closer than this are one line
MIN_BAND_H_PX = 7               # noise floor for a text line
MIN_BAND_INK = 6                # min dark px in a row to count as ink
COL_INK_MIN = 2                 # min dark px in a col to call it started
PAGE_CLUSTER_TOL = 0.015        # within-page left-edge drift tolerance (scan skew ~0.01, level steps >=0.03)


class LayoutUnavailable(Exception):
    pass


def layout_boxes(png_bytes: bytes) -> dict:
    """Call the configured stage-1 layout provider.

    The provider contract returns ``{width, height, boxes}``; transport,
    endpoint, and retry policy are owned by the shared adapter.
    """
    t0 = time.time()
    try:
        data = post_multipart(png_bytes, role=LAYOUT_ROLE)
    except Exception as exc:  # noqa: BLE001 - caller decides whether to fall back
        raise LayoutUnavailable(f"layout provider failed: {exc}") from exc
    logger.info("layout ok in %.1fs (%d boxes)", time.time() - t0, len(data.get("boxes", [])))
    return data


def _content_region(boxes: list[dict], w: int, h: int) -> tuple[int, int, int, int] | None:
    """Largest CONTENT-ish box, shrunk by any edge-label boxes it contains."""
    cands = [b for b in boxes if b["label"].lower() in CONTENT_LABELS
             and (b["bbox"][2] - b["bbox"][0]) > w * 0.3
             and (b["bbox"][3] - b["bbox"][1]) > h * 0.15]
    if not cands:
        return None
    x1, y1, x2, y2 = max(cands, key=lambda b: (b["bbox"][2] - b["bbox"][0]) * (b["bbox"][3] - b["bbox"][1]))["bbox"]
    for b in boxes:
        if b["label"].lower() not in EDGE_LABELS:
            continue
        bx1, by1, bx2, by2 = b["bbox"]
        if bx1 >= x1 - 5 and bx2 <= x2 + 5:      # horizontally inside
            if abs(by2 - y1) < h * 0.06:         # sits just above content -> header
                y1 = max(y1, by2 + 4)
            elif abs(by1 - y2) < h * 0.06:       # just below -> footer/pagenum
                y2 = min(y2, by1 - 4)
    return [max(0, int(x1)), max(0, int(y1)), min(w, int(x2)), min(h, int(y2))]


def _gray_matrix(page, dpi: int):
    """Render page, return (gray 2D list-of-rows as bytearrays, scale, png)."""
    import pymupdf as fitz

    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
    w, h, n = pix.width, pix.height, pix.n
    smp = pix.samples
    rows = []
    stride = w * n
    for y in range(h):
        rows.append(smp[y * stride:(y + 1) * stride])
    return rows, w, h


def _line_bands(rows, x_lo: int, x_hi: int, y_lo: int, y_hi: int):
    """Split the region into text-line bands via row ink profile."""
    DARK = 140
    prof = []
    for y in range(y_lo, y_hi):
        row = rows[y]
        c = 0
        for x in range(x_lo, x_hi):
            if row[x] < DARK:
                c += 1
        prof.append(c)
    bands = []
    y = 0
    total = len(prof)
    while y < total:
        if prof[y] >= MIN_BAND_INK:
            y0 = y
            gap = 0
            y1 = y
            while y < total:
                if prof[y] >= MIN_BAND_INK:
                    y1 = y
                    gap = 0
                else:
                    gap += 1
                    if gap > LINE_MERGE_GAP_PX:
                        break
                y += 1
            if y1 - y0 + 1 >= MIN_BAND_H_PX:
                bands.append((y_lo + y0, y_lo + y1))
        else:
            y += 1
    return bands


def _band_left_edge(rows, y0: int, y1: int, x_lo: int, x_hi: int) -> int | None:
    """Leftmost ink column of a band (indentation evidence)."""
    DARK = 140
    for x in range(x_lo, x_hi):
        c = 0
        for y in range(y0, y1 + 1):
            if rows[y][x] < DARK:
                c += 1
                if c >= COL_INK_MIN:
                    return x
        # sparse columns skipped; continue scanning right
    return None


def extract_toc_rows(page, dpi: int = 200) -> tuple[list[dict], dict]:
    """One TOC physical page -> ordered rows [{y0,y1,x_left,png}] + meta.

    Raises LayoutUnavailable when the llama-swap layout endpoint fails;
    caller decides fallback.
    """
    png = render_page_png(page, dpi=dpi)
    lay = layout_boxes(png)
    w, h = lay.get("width"), lay.get("height")
    boxes = lay.get("boxes") or []
    region = _content_region(boxes, w, h)
    if region is None:
        raise LayoutUnavailable("no usable content box on this page")
    rx1, ry1, rx2, ry2 = region

    rows_mat, W, H = _gray_matrix(page, dpi)
    sx, sy = W / w, H / h
    bands = _line_bands(rows_mat, int(rx1 * sx), int(rx2 * sx), int(ry1 * sy), int(ry2 * sy))
    out = []
    page_w_pt = page.rect.width
    for (by0, by1) in bands:
        xl = _band_left_edge(rows_mat, by0, by1, int(rx1 * sx), int(rx2 * sx))
        if xl is None:
            continue
        pad = 4
        clip_y0 = max(0, by0 - pad)
        clip_y1 = min(H, by1 + pad)
        import pymupdf as fitz

        pt = 72.0 / dpi
        x_lo_pt = max(0.0, rx1 * sx * pt - 12)  # 左留 12pt：通栏章行的首字（第）不能被裁
        x_hi_pt = min(page.rect.width, rx2 * sx * pt + 14)  # 右留 14pt：三位数页码不能截尾
        clip = fitz.Rect(x_lo_pt, clip_y0 * pt, x_hi_pt, clip_y1 * pt)
        strip = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), clip=clip)
        # 右缘数字条：整行转录偶尔把行尾页码挤到下一行或吞进点线里，
        # 单独裁最右 ~11% 再读一遍，按行序配对（见 recover_numbers）
        region_w_pt = (rx2 - rx1) * sx * pt
        num_lo = max(x_lo_pt, x_hi_pt - region_w_pt * 0.11)
        right = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72),
                                clip=fitz.Rect(num_lo, clip_y0 * pt,
                                               min(page_w_pt, rx2 * sx * pt + 6), clip_y1 * pt))
        out.append({
            "y0": by0, "y1": by1,
            # 绝对墨缘（渲染像素）：跨页归一化在 assign_levels 里做——
            # pp-doclayout 的内容框每页左右不一，框内相对值会把通栏章行
            # 挤到下一级的聚类里（第3章实测教训）
            "x_abs": xl,
            "render_w": W,
            "x_left_norm": round(xl / max(1, W), 4),
            "png": strip.tobytes("png"),
            "right_png": right.tobytes("png"),
        })
    meta = {"region": region, "img_size": [w, h], "render_size": [W, H],
            "rows": len(out)}
    logger.info("toc rows on page: %d (region=%s)", len(out), region)
    return out, meta


AV_FALLBACK_LANGS = ["zh-Hans", "ja", "en"]  # ja-first was corrupting 简->日 variants


def ocr_png_vl_first(png: bytes) -> str:
    """Read one strip/page through the configured visual provider.

    The native platform adapter remains the offline fallback so title matching
    uses one configured transcription path whenever it is available.
    """
    from ._vlhttp import OCRError

    try:
        return post_image(provider_model("toc", "toc-ocr"), png, "OCR:", role="toc").strip()
    except Exception:  # noqa: BLE001 - provider unavailable -> native fallback
        from . import ocr_apple

        try:
            return ocr_apple.ocr_png(png, langs=AV_FALLBACK_LANGS)
        except Exception:  # noqa: BLE001
            return ""


def ocr_rows(rows: list[dict], workers: int = 4) -> tuple[list[str], list[int | None]]:
    """Row-strip recognition through the configured provider and native fallback.

    Also reads each row's right-edge number strip.

    Returns (row_texts, per_row_numbers). Numbers are read independently per
    row (the strip shares the row's y-band, so no cross-row pairing is
    needed); callers filter junk bands FIRST and only then apply the
    monotonic gate over surviving entries (see inject_toc).
    """
    import concurrent.futures as cf

    def work(args: tuple[bytes, bytes]) -> tuple[str, int | None]:
        png, rpng = args
        txt = ocr_png_vl_first(png)
        num = None
        try:
            rtxt = ocr_png_vl_first(rpng)
            cands = [int(m) for m in re.findall(r"\d{1,3}", rtxt or "")
                     if 1 <= int(m) <= 2000]
            if cands:
                num = cands[-1]
        except Exception:  # noqa: BLE001
            pass
        return txt, num

    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        pairs = list(ex.map(work, [(r["png"], r.get("right_png")) for r in rows]))
    return [t for t, _ in pairs], [n for _, n in pairs]


def assign_levels(all_rows: list[list[dict]]) -> None:
    """Assign hierarchy levels from left edges, in place. Two stages:

    1. cluster WITHIN each page (jitter there is tiny);
    2. use the page with the most distinct starts as reference; every other
       page's cluster snaps to the NEAREST reference start when within
       tolerance (=40% of the reference level step) - this absorbs
       cross-page scan drift that defeats naive global single-linkage.
    A reference with a single start means the book prints everything
    flush-left -> level stays None, caller falls back to numbering
    semantics.
    """
    page_starts: list[tuple[list[dict], list[float]]] = []
    # 全局最左墨缘做统一基准：内容框每页不同，必须先对齐再聚类
    if any(r.get("x_abs") is not None for pg in all_rows for r in pg):
        gmin = min(r["x_abs"] for pg in all_rows for r in pg
                   if r.get("x_abs") is not None)
        for pg in all_rows:
            for r in pg:
                if r.get("x_abs") is not None:
                    r["x_left_norm"] = round((r["x_abs"] - gmin) /
                                             max(1, r.get("render_w") or 1), 4)
    for pg in all_rows:
        xs = sorted({r["x_left_norm"] for r in pg})
        if not xs:
            page_starts.append((pg, []))
            continue
        groups = [[xs[0]]]
        for a, b in zip(xs, xs[1:]):
            if b - a >= PAGE_CLUSTER_TOL:
                groups.append([b])
            else:
                groups[-1].append(b)
        page_starts.append((pg, [sum(g) / len(g) for g in groups]))

    ref_pg, ref_starts = max(page_starts, key=lambda t: len(t[1]))
    if len(ref_starts) <= 1:
        for pg, _ in page_starts:
            for r in pg:
                r["level"] = None
        logger.info("level clusters: 1 (flush-left TOC) -> numbering fallback")
        return

    steps = [b - a for a, b in zip(ref_starts, ref_starts[1:])]
    step = min(steps) if steps else 0.05
    tol = 0.4 * step
    for pg, starts in page_starts:
        for r in pg:
            x = r["x_left_norm"]
            near = min(range(len(ref_starts)), key=lambda i: abs(x - ref_starts[i]))
            r["level"] = near + 1
    logger.info("level clusters: %d %s (tol=%.3f)", len(ref_starts),
                [round(s, 3) for s in ref_starts], tol)
