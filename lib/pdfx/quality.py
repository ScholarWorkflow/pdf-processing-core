"""Per-page text-quality scoring and page classification for pdfx.

Tiers:
  trusted   - text layer is usable as-is
  washable  - text present but noisy (e.g. spaces between Han characters)
  untrusted - garbled text layer, must be re-recognized visually
  empty     - no usable text layer, must be OCR'd

Thresholds are calibrated against a representative synthetic textbook
benchmark; garble and CJK-space distributions remain well below the production
thresholds.
thresholds sit well above the bulk while still catching real outliers.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

GARBLE_THRESHOLD = 0.15
CJK_SPACE_THRESHOLD = 0.05
EMPTY_CHARS = 8

RE_FFFD = "\ufffd"
_HAN = r"\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF"
_KANA = r"\u3040-\u30FF"
_HANGUL = r"\uAC00-\uD7AF"
_CJK_PUNCT = r"\u3000-\u303F\uFF01-\uFF1F\uFF08-\uFF09\uFF0C\uFF1A\uFF1B\uFF1E\uFF1C"
_READABLE = (
    r"0-9A-Za-z"
    + _HAN
    + _KANA
    + _HANGUL
    + _CJK_PUNCT
    + r"\uFF02-\uFF03\uFF05-\uFF07\uFF0A\uFF0B\uFF0D\uFF0F\uFF1C-\uFF1E\uFF20\uFF3B\uFF3D\uFF5B\uFF5D"
    + r"\s.,;:!?'\"()\[\]{}<>/@#%&*+=\-_|~^`$\\\\"
)

RE_SPACED_HAN = re.compile(rf"(?<=[{_HAN}])[ \t\u00a0\u3000]+(?=[{_HAN}])")
RE_HAN_COUNT = re.compile(rf"[{_HAN}]")

# CJK/kana prose skeleton (moved here from texlayer_audit so lightweight
# consumers like formula_regions can share it without importing OCR engines).
_HAN_KANA_RE = re.compile(r"[^\u3040-\u30FF\u3400-\u9FFF\uF900-\uFAFF]+")


def cjk_skeleton(text: str) -> str:
    """CJK/kana prose skeleton: what both engines read reliably."""
    return _HAN_KANA_RE.sub("", unicodedata.normalize("NFKC", text or ""))
RE_ILLEGAL = re.compile(
    "[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f"
    "\ue000-\uf8ff"
    "\ufdd0-\ufdef"
    "\ufffe\ufffe\uffff\uffff]"
)

# Scanned pages sometimes carry ONLY a page-number text layer ("12", "--- 3 ---")
# while the content is an image. Such stubs must route to the OCR/vision chain.
RE_PAGE_NUMBER_STUB = re.compile(r"[\d\s\-—–.，。、:：/\\()（）]+")


@dataclass
class PageQuality:
    page: int
    chars: int = 0
    garble: float = 0.0
    cjk_space_rate: float = 0.0
    images: int = 0
    tier: str = "empty"
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "page": self.page,
            "chars": self.chars,
            "tier": self.tier,
            "garble": round(self.garble, 4),
            "cjk_space_rate": round(self.cjk_space_rate, 4),
            "images": self.images,
            "detail": self.detail,
        }


def _line_bad_share(text: str) -> float:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return 0.0
    readable_re = re.compile(rf"[{_READABLE}]")
    bad = 0
    for ln in lines:
        if len(ln) < 2:
            continue
        n = len(readable_re.findall(ln))
        if n / len(ln) < 0.4:
            bad += 1
    return bad / len(lines)


def score_text(text: str) -> tuple[float, float, dict]:
    """Return (garble_score, cjk_space_rate, detail) for a page's text."""
    if not text or not text.strip():
        return 0.0, 0.0, {"reason": "no text"}

    total = len(text)
    fffd = text.count(RE_FFFD)
    illegal = len(RE_ILLEGAL.findall(text))
    bad_line_share = _line_bad_share(text)

    garble = min(1.0, 0.40 * (fffd / total) + 0.35 * (illegal / total) + 0.25 * bad_line_share)

    han_count = len(RE_HAN_COUNT.findall(text))
    spaced_pairs = len(RE_SPACED_HAN.findall(text))
    cjk_rate = spaced_pairs / han_count if han_count >= 10 else 0.0

    detail = {
        "fffd": fffd,
        "illegal": illegal,
        "bad_line_share": round(bad_line_share, 4),
        "han_count": han_count,
        "spaced_han_pairs": spaced_pairs,
    }
    return garble, cjk_rate, detail


def classify_tier(chars: int, garble: float, cjk_space_rate: float) -> str:
    if chars < EMPTY_CHARS:
        return "empty"
    if garble >= GARBLE_THRESHOLD:
        return "untrusted"
    if cjk_space_rate >= CJK_SPACE_THRESHOLD:
        return "washable"
    return "trusted"


def score_page(page, page_number: int, count_images: bool = True) -> PageQuality:
    try:
        text = page.get_text() or ""
    except Exception:
        text = ""
    text = unicodedata.normalize("NFC", text)
    stripped = text.strip()
    if stripped and len(stripped) < 15 and RE_PAGE_NUMBER_STUB.fullmatch(stripped):
        garble, cjk_rate, detail = 0.0, 0.0, {"reason": "page-number stub"}
        images = len(page.get_images(full=True)) if count_images else 0
        q = PageQuality(page=page_number, chars=0, garble=garble,
                        cjk_space_rate=cjk_rate, images=images, tier="empty", detail=detail)
        return q
    garble, cjk_rate, detail = score_text(text)
    images = len(page.get_images(full=True)) if count_images else 0
    chars = len(text.strip())
    q = PageQuality(
        page=page_number,
        chars=chars,
        garble=garble,
        cjk_space_rate=cjk_rate,
        images=images,
        detail=detail,
    )
    q.tier = classify_tier(chars, garble, cjk_rate)
    return q


def scan_pdf(pdf_path: str) -> list:
    """Score every page of a PDF; returns list[PageQuality]."""
    import pymupdf as fitz

    qualities = []
    doc = fitz.open(pdf_path)
    for i in range(len(doc)):
        qualities.append(score_page(doc[i], i + 1))
    doc.close()
    return qualities


def classify_pages(pdf_path: str) -> tuple:
    """Legacy classification by image coverage: (scanned_pages, digital_pages), 0-based."""
    import pymupdf as fitz

    scanned, digital = [], []
    doc = fitz.open(pdf_path)
    for i in range(len(doc)):
        page = doc[i]
        page_area = page.rect.width * page.rect.height
        total_img_area = 0.0
        for img in page.get_images(full=True):
            xref = img[0]
            for r in page.get_image_rects(xref):
                total_img_area += r.width * r.height
        coverage = total_img_area / page_area if page_area > 0 else 0
        (scanned if coverage > 0.9 else digital).append(i)
    doc.close()
    return scanned, digital


def summarize(qualities: list) -> dict:
    tiers = {}
    for q in qualities:
        tiers[q.tier] = tiers.get(q.tier, 0) + 1
    return {"total_pages": len(qualities), "tiers": tiers}
