"""Unified extraction entry point.

strategy="fast": legacy behaviour — PyMuPDF text layer kept whenever non-empty;
empty pages fall back to pypdf (digital-looking) or Apple Vision (image-covered).
tesseract intentionally removed even here (approved deviation).

strategy="auto": per-page quality routing.
  trusted   -> text layer as-is
  washable  -> clean.py wash, re-check, else vision chain
  untrusted / empty -> vision chain

Vision chain order (first success wins): configured layout-aware provider ->
configured transcription provider -> native platform OCR. Remote providers
are optional and must be configured by the caller.

Output is an in-memory report dict; file formats remain the caller shells'
responsibility (contract: downstream output unchanged).
"""

from __future__ import annotations

import logging
import time
from collections import Counter

from . import clean as clean_mod
from .ocr_apple import LEGACY_LANGS
from .quality import (
    GARBLE_THRESHOLD,
    CJK_SPACE_THRESHOLD,
    classify_pages,
    score_page,
    score_text,
)

logger = logging.getLogger(__name__)

DEFAULT_ENGINES = ("layout", "transcription", "native")


def _run_engine(name: str, page, dpi: int, apple_langs: list | None = None, want_conf: bool = False):
    # Keep historical engine identifiers accepted by --engines while exposing
    # role-oriented defaults to new callers.
    role = {
        "layout": "layout",
        "transcription": "transcription",
        "native": "native",
        "doc-parser": "layout",
        "qwen3-vl": "transcription",
        "apple": "native",
    }.get(name)
    if role == "layout":
        from . import ocr_docparser

        return ocr_docparser.ocr_page(page, dpi=dpi)
    if role == "transcription":
        from . import ocr_qwen_vl

        return ocr_qwen_vl.ocr_page(page, dpi=dpi)
    if role == "native":
        from . import ocr_apple

        return ocr_apple.ocr_page(page, langs=apple_langs, return_conf=want_conf)
    raise ValueError(f"unknown vision engine: {name}")


def _vision_chain(page, engines, dpi: int, apple_langs: list | None = None,
                  want_conf: bool = False) -> tuple:
    """Return (text|None, method, meta) trying each engine in order.

    meta carries per-page extras (e.g. {"mean_conf": float} for Apple Vision).
    """
    errors = []
    for name in engines:
        try:
            t0 = time.time()
            out = _run_engine(name, page, dpi, apple_langs=apple_langs, want_conf=want_conf)
            if want_conf and name in {"native", "apple"}:
                text, mean_conf = out
                meta = {"mean_conf": mean_conf}
            else:
                text, meta = out, {}
            logger.info("    engine %s ok in %.1fs (%d chars)", name, time.time() - t0, len(text))
            return text.strip(), f"vision:{name}", meta
        except Exception as e:
            logger.warning("    engine %s failed: %s", name, e)
            errors.append(f"{name}: {e}")
    return None, "vision-failed:" + ";".join(errors)[:200], {}


def _pypdf_text(pdf_path: str, idx0: int) -> str | None:
    try:
        from pypdf import PdfReader
    except ImportError:
        return None
    try:
        reader = PdfReader(pdf_path)
        text = reader.pages[idx0].extract_text()
        return text.strip() if text and text.strip() else None
    except Exception:
        return None


def extract_pdf(
    pdf_path: str,
    strategy: str = "auto",
    dpi: int = 200,
    engines: tuple = DEFAULT_ENGINES,
    markers: bool = True,
    max_vision_pages: int | None = None,
    sep: str = "\n",
) -> dict:
    import pymupdf as fitz

    assert strategy in ("auto", "fast"), f"bad strategy: {strategy}"
    if strategy == "fast":
        engines = ("native",)
    t_start = time.time()
    methods = Counter()
    records = []
    texts = []
    page_texts = []
    scanned_cache = None
    vision_budget = max_vision_pages

    doc = fitz.open(pdf_path)
    total = len(doc)
    logger.info("pdfx.extract: %s (%d pages, strategy=%s)", pdf_path, total, strategy)

    for i in range(total):
        phys = i + 1
        q = score_page(doc[i], phys)
        text = ""
        method = ""
        meta = {}

        if strategy == "fast":
            if q.chars > 0:
                text, method = (doc[i].get_text() or "").strip(), "PyMuPDF"
            else:
                if scanned_cache is None:
                    s, d = classify_pages(pdf_path)
                    scanned_cache = set(s)
                if i in scanned_cache:
                    got, m, meta = _vision_chain(doc[i], engines, dpi,
                                                 apple_langs=LEGACY_LANGS, want_conf=True)
                else:
                    got = _pypdf_text(pdf_path, i)
                    m, meta = "pypdf", {}
                    if got is None:
                        got, m2, meta = _vision_chain(doc[i], engines, dpi,
                                                      apple_langs=LEGACY_LANGS, want_conf=True)
                        m = m2 if got is not None else m
                text, method = (got, m) if got is not None else ("", "none")
        else:
            if q.tier == "trusted":
                text = (doc[i].get_text() or "").strip()
                method = "PyMuPDF"
            elif q.tier == "washable":
                washed = clean_mod.clean_text(doc[i].get_text() or "")
                g2, c2, _ = score_text(washed)
                if g2 < GARBLE_THRESHOLD and c2 < CJK_SPACE_THRESHOLD:
                    text, method = washed.strip(), "PyMuPDF+clean"
                else:
                    got, m, meta = _vision_chain(doc[i], engines, dpi)
                    text, method = (got, m) if got is not None else (washed.strip(), "clean-only")
            else:
                if vision_budget is not None and vision_budget <= 0:
                    text, method = "", "vision-skipped(budget)"
                else:
                    if vision_budget is not None:
                        vision_budget -= 1
                    got, m, meta = _vision_chain(doc[i], engines, dpi)
                    text, method = (got, m) if got is not None else ("", "none")

        stat_key = method.split(";")[0]
        if len(stat_key) > 60:
            stat_key = stat_key[:57] + "..."
        methods[stat_key] += 1
        rec = q.to_dict()
        rec["method"] = method
        records.append(rec)
        page_texts.append({
            "page": phys,
            "method": method,
            "text": text,
            "mean_conf": meta.get("mean_conf"),
        })

        body = text if text else f"[No text extracted from page {phys}]"
        texts.append(f"<!-- PDF_PAGE: {phys} -->\n\n{body}" if markers else body)

        if phys % 20 == 0 or phys == total:
            logger.info("  %d/%d pages done (%.0fs elapsed)", phys, total, time.time() - t_start)

    doc.close()

    from ._vlhttp import http_stats

    return {
        "pdf": pdf_path,
        "total_pages": total,
        "strategy": strategy,
        "elapsed_s": round(time.time() - t_start, 1),
        "stats": {"methods": dict(methods)},
        "vision_http": http_stats(),
        "pages": records,
        "page_texts": page_texts,
        "text": ("\n\n".join(texts) if markers else sep.join(texts)),
    }
