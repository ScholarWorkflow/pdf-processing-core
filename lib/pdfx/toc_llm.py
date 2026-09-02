"""Provider-backed table-of-contents understanding (v3).

Why this exists: the v1/v2 pipeline parsed transcribed TOC lines with fixed
rules (regex line shapes + geometric clustering). Real books print their
TOC in thousands of typographies; any rule set fits only the book it was
tuned on one narrow layout. Understanding arbitrary TOC layout is exactly
what a vision-LLM does well, so the LLM now owns comprehension while the
script keeps ownership of verification.

Pipeline position: inject_toc.run() step 3 calls parse_toc_pages() with
rendered TOC page images BEFORE any rule-based parsing. Pages whose chunk
passes validation never touch the rule parser; failing chunks fall back to
the legacy layout/transcript path unchanged.

Model access uses the shared visual-provider contract. Providers can declare
``structured: false`` in the configured chain when they cannot follow the
JSON-output prompt; no provider, pricing, or deployment assumptions belong in
this module.
"""

from __future__ import annotations

import io
import json
import logging
import re

from ._vlhttp import OCRError, configured_models, post_image

logger = logging.getLogger(__name__)

MAX_IMAGES_PER_CALL = 1      # one page per call: exact page attribution,
                             # shorter outputs (truncation-safe)
MAX_TOKENS_PER_CALL = 8192
MAX_SIDE_PX = 1800           # keep small TOC print legible after downscale
MAX_ATTEMPTS = 2             # whole-batch retries (chain walk is internal)

PROMPT = """These are scanned page(s) {pages} of a book's table of contents \
(目次/目录/Contents). Extract EVERY content entry visible on these pages, in \
visual reading order (top-to-bottom; multi-column: finish one column first).

Return ONLY a JSON array - no markdown fence, no commentary - one object per \
entry:
{{"title": "<verbatim title, original language, WITHOUT dot leaders or \
trailing page number>", "level": <1|2|3>, "printed_page": <integer printed \
page number at the end of that line, or null>}}

Level rules:
- 1 = top-level unit: 第N章 / 第N部 / Chapter N / a bare "N Title" chapter line
- 2 = section: "N.N" pattern, or an unnumbered heading sitting directly under \
a level-1 unit (e.g. コラム/演習問題 blocks)
- 3 = subsection: "N.N.N" or deeper dotted numbering

Other rules:
- printed_page: the right-hand page number printed for that entry. null only \
when the line truly has none.
- Do NOT include the 目次/Contents heading itself, running headers/footers, \
or page numbers alone.
- Do NOT invent entries that are not visible. If a title is partly unreadable, \
transcribe the readable part faithfully.
- Titles stay in the book's own language."""


class TocLlmError(Exception):
    pass


# 模型偶尔把标题写成 LaTeX（$\chi^2$ 分布）；页面原文是 Unicode。
# 写大纲前统一还原成可读字符，也让下游 _match_title 能对上页面文本。
_LATEX_MACROS = {
    "\\chi": "χ", "\\alpha": "α", "\\beta": "β", "\\gamma": "γ", "\\delta": "δ",
    "\\epsilon": "ε", "\\varepsilon": "ε", "\\zeta": "ζ", "\\eta": "η",
    "\\theta": "θ", "\\kappa": "κ", "\\lambda": "λ", "\\mu": "μ", "\\nu": "ν",
    "\\xi": "ξ", "\\pi": "π", "\\rho": "ρ", "\\sigma": "σ", "\\tau": "τ",
    "\\upsilon": "υ", "\\phi": "φ", "\\varphi": "φ",
    "\\Delta": "Δ", "\\Omega": "Ω", "\\Sigma": "Σ", "\\Gamma": "Γ", "\\Theta": "Θ",
    "\\Lambda": "Λ", "\\Phi": "Φ", "\\Psi": "Ψ",
    "\\times": "×", "\\cdot": "·", "\\pm": "±", "\\leq": "≤", "\\geq": "≥",
    "\\neq": "≠", "\\approx": "≈", "\\infty": "∞", "\\ldots": "…", "\\dots": "…",
    "\\hat": "", "\\bar": "", "\\vec": "", "\\mathrm": "", "\\mathbf": "",
    "\\text": "", "\\left": "", "\\right": "", "\\,": " ", "\\;": " ", "\\!": "",
}


def _strip_latex(title: str) -> str:
    t = title
    for pat in ("$\\!", "$$", "$", "\\(", "\\)", "\\[", "\\]", "~"):
        t = t.replace(pat, " ")
    for k in sorted(_LATEX_MACROS, key=len, reverse=True):
        t = t.replace(k, _LATEX_MACROS[k])
    # \hat{y} / x^{2} / _{n} 这类残余包装符号
    t = re.sub(r"[{}^_]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _prepare_image(png_bytes: bytes) -> tuple[bytes, str]:
    """Downscale a page image to the provider-safe size (Pillow optional)."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(png_bytes)) as im:
            if max(im.size) > MAX_SIDE_PX:
                ratio = MAX_SIDE_PX / max(im.size)
                im = im.resize((round(im.width * ratio), round(im.height * ratio)),
                               Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            im.convert("RGB").save(buf, format="JPEG", quality=92)
            data, mime = buf.getvalue(), "image/jpeg"
    except ImportError:
        data, mime = png_bytes, "image/png"
    return data, mime


def _extract_json_array(text: str) -> list:
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```\s*$", "", t)
    start, end = t.find("["), t.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise TocLlmError(f"no JSON array in model output ({len(t)} chars)")
    try:
        arr = json.loads(t[start:end + 1])
    except json.JSONDecodeError as e:
        raise TocLlmError(f"invalid JSON: {e}") from e
    if not isinstance(arr, list):
        raise TocLlmError("model output is not a JSON array")
    return arr


def _normalize_entry(obj, src_page: int, total_pages: int) -> dict | None:
    if not isinstance(obj, dict):
        return None
    title = str(obj.get("title", "")).strip()
    title = _strip_latex(title)
    # 防御：模型偶尔把点线和页码一起塞进 title，兜底剥离「点线+数字」尾巴
    title = re.sub(r"[\s·•⋅‧‥….\u3002_\-–—―]{1,}\d{1,4}\s*$", "", title)
    title = re.sub(r"[\s·•⋅‧‥….\u3002_\-–—―]+$", "", title).strip()
    if not title or not re.search(r"[\u3040-\u9fffA-Za-z0-9]", title):
        return None
    try:
        level = int(obj.get("level", 2))
    except (TypeError, ValueError):
        level = 2
    level = max(1, min(3, level))
    page = obj.get("printed_page")
    if isinstance(page, str):
        page = page.strip()
        page = int(page) if re.fullmatch(r"\d{1,4}", page) else None
    elif isinstance(page, bool) or not isinstance(page, int):
        page = None
    if page is not None and not (0 < page <= total_pages * 3):
        logger.info("toc-llm: clamping implausible printed_page %s on %r",
                    page, title[:24])
        page = None
    return {"level": level, "title": title, "printed": page,
            "low_confidence": False, "_src_page": src_page, "_num_src": "llm"}


def _validate_chunk(entries: list[dict], n_pages: int) -> str | None:
    """Return a failure reason string, or None when the chunk looks sane."""
    if len(entries) < max(4, 2 * n_pages):
        return f"too few entries ({len(entries)}) for {n_pages} TOC page(s)"
    numbered = [e for e in entries if e.get("printed") is not None]
    if len(numbered) < 0.5 * len(entries):
        return f"only {len(numbered)}/{len(entries)} entries carry printed pages"
    seq = [e["printed"] for e in entries if e.get("printed") is not None]
    drops = sum(1 for a, b in zip(seq, seq[1:]) if b < a)
    if drops > 0.2 * max(1, len(seq) - 1):
        return f"printed-page sequence too noisy ({drops} inversions)"
    return None


def parse_toc_pages(page_images: list[tuple[int, bytes]], total_pages: int
                    ) -> tuple[dict[int, list[dict]], dict]:
    """Understand TOC pages with the vision-LLM chain.

    page_images: [(physical_page, png_bytes)] in reading order.
    Returns ({physical_page: [entry, ...]}, meta). Raises nothing - chunks
    that fail get logged and simply omitted (caller falls back per page).
    """
    models = configured_models("toc", structured=True)
    if not models:
        raise RuntimeError(
            "no structured visual provider is configured; set PDFX_VISION_CHAIN"
        )
    result: dict[int, list[dict]] = {}
    attempts_log: list[dict] = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        pending = [(phys, png) for phys, png in page_images if phys not in result]
        if not pending:
            break
        saw_provider_error = False
        for i in range(0, len(pending), MAX_IMAGES_PER_CALL):
            chunk = pending[i:i + MAX_IMAGES_PER_CALL]
            phys_list = [p for p, _ in chunk]
            prompt = PROMPT.format(pages=", ".join(map(str, phys_list)))
            text = None
            provider_error = None
            for model in models:
                try:
                    image, mime = _prepare_image(chunk[0][1])
                    text = post_image(model, image, prompt,
                                      max_tokens=MAX_TOKENS_PER_CALL,
                                      role="toc", image_mime=mime)
                    break
                except OCRError as exc:
                    provider_error = exc
            if text is None:
                error = provider_error or RuntimeError("provider chain returned no result")
                logger.warning("toc-llm: provider chain failed on pages %s: %s",
                               phys_list, error)
                attempts_log.append({"pages": phys_list, "error": str(error)})
                saw_provider_error = True
                continue
            try:
                arr = _extract_json_array(text)
            except TocLlmError as e:
                logger.warning("toc-llm: %s on pages %s (attempt %d)",
                               e, phys_list, attempt)
                attempts_log.append({"pages": phys_list, "error": str(e)})
                continue
            ents = []
            for obj in arr:
                ent = _normalize_entry(obj, phys_list[0], total_pages)
                if ent is not None:
                    ents.append(ent)
            seen: set[tuple[str, int | None]] = set()
            deduped = []
            for e in ents:
                key = (e["title"], e.get("printed"))
                if key not in seen:
                    seen.add(key)
                    deduped.append(e)
            ents = deduped
            reason = _validate_chunk(ents, len(chunk))
            if reason:
                logger.warning("toc-llm: chunk pages %s rejected: %s "
                               "(%d raw entries)", phys_list, reason, len(arr))
                attempts_log.append({"pages": phys_list, "error": reason})
                continue
            for p in phys_list:
                result[p] = [dict(e, _src_page=p) for e in ents]
            logger.info("toc-llm: pages %s -> %d entries (attempt %d)",
                        phys_list, len(ents), attempt)
        if not saw_provider_error and not result:
            logger.info("toc-llm: provider returned no valid entries; skipping retries")
            break

    meta = {
        "pages_parsed": sorted(result),
        "entries": sum(len(v) for v in result.values()),
        "attempts": attempts_log,
        "configured_models": models,
    }
    return result, meta
