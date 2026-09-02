"""TOC read/write, sidecar backup/restore, and SEGMENTED page-offset detection.

Offset convention: printed_page = physical_page - offset.

Unlike pdf-book-splitter's detect_page_offset (single int), this module
returns segments so books with roman-numeral front matter get a second
offset region.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections import Counter
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

MIN_RUN = 4
ROMAN_RE = re.compile(r"^[ivxlcdm]{2,8}$|^[IVXLCDM]{2,8}$")
ARABIC_RE = re.compile(r"^\d{1,4}$")
_ROMAN_VALUES = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}


def roman_to_int(s: str) -> int | None:
    s = s.lower()
    if not ROMAN_RE.match(s):
        return None
    vals = [_ROMAN_VALUES[c] for c in s]
    total = 0
    for i, v in enumerate(vals):
        if i + 1 < len(vals) and v < vals[i + 1]:
            total -= v
        else:
            total += v
    return total if total >= 1 else None


def _candidate_printed_numbers(page) -> list:
    try:
        text = page.get_text() or ""
    except Exception:
        return []
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []
    window = lines[:3] + lines[-3:]
    out = []
    for ln in window:
        if ARABIC_RE.match(ln):
            out.append(int(ln))
        elif ROMAN_RE.match(ln):
            v = roman_to_int(ln)
            if v:
                out.append(v)
    return out


def _find_runs(per_page: list, total: int, min_run: int) -> list:
    runs = []
    i = 1
    while i <= total:
        off = per_page[i]
        if off is None:
            i += 1
            continue
        j = i
        while j + 1 <= total and per_page[j + 1] == off:
            j += 1
        if j - i + 1 >= min_run:
            runs.append([i, j, off])
        i = j + 1
    return runs


def detect_page_segments(pdf_path: str, min_run: int = MIN_RUN) -> list:
    """Return [{"start","end","offset"}], physical pages 1-based inclusive."""
    import pymupdf as fitz

    doc = fitz.open(pdf_path)
    total = len(doc)
    per_page = [None] * (total + 1)

    for idx0 in range(total):
        phys = idx0 + 1
        votes = Counter()
        for n in _candidate_printed_numbers(doc[idx0]):
            if not (1 <= n <= total + 5):
                continue
            off = phys - n
            if 0 <= off <= phys - 1:
                votes[off] += 1
        if votes:
            per_page[phys] = votes.most_common(1)[0][0]
    doc.close()

    runs = _find_runs(per_page, total, min_run)
    if not runs:
        return [{"start": 1, "end": total, "offset": 0}]

    segments = []
    prev_end = 0
    for k, (s, e, off) in enumerate(runs):
        if s > prev_end + 1:
            gap_lo, gap_hi = prev_end + 1, s - 1
            gap = gap_hi - gap_lo + 1
            if segments:
                half = gap // 2
                if half > 0:
                    segments.append(
                        {"start": gap_lo, "end": gap_lo + half - 1, "offset": segments[-1]["offset"]}
                    )
                rest_lo = gap_lo + half
                if rest_lo <= gap_hi:
                    nxt_off = off
                    segments.append({"start": rest_lo, "end": gap_hi, "offset": nxt_off})
            else:
                segments.append({"start": gap_lo, "end": gap_hi, "offset": off})
        segments.append({"start": s, "end": e, "offset": off})
        prev_end = e

    if prev_end < total:
        segments.append({"start": prev_end + 1, "end": total, "offset": segments[-1]["offset"]})

    merged = []
    for seg in sorted(segments, key=lambda x: x["start"]):
        if merged and merged[-1]["offset"] == seg["offset"]:
            merged[-1]["end"] = max(merged[-1]["end"], seg["end"])
        else:
            merged.append(seg)

    merged[0]["start"] = 1
    merged[-1]["end"] = total
    return merged


def resolve_physical(printed: int, segments: list) -> int:
    """Map a printed page number to its physical page using segments."""
    for seg in segments:
        lo_printed = seg["start"] - seg["offset"]
        hi_printed = seg["end"] - seg["offset"]
        if lo_printed <= printed <= hi_printed:
            return printed + seg["offset"]
    return max(1, min(printed + segments[-1]["offset"], segments[-1]["end"]))


def get_toc(pdf_path: str) -> list:
    import pymupdf as fitz

    doc = fitz.open(pdf_path)
    toc = doc.get_toc()
    doc.close()
    return toc


def set_toc(pdf_path: str, toc: list) -> None:
    """Write outline IN-PLACE via incremental save. Content streams untouched."""
    import pymupdf as fitz

    doc = fitz.open(pdf_path)
    total = len(doc)
    for level, title, page in toc:
        if not (1 <= page <= total):
            raise ValueError(f"TOC entry page out of range: {title!r} -> {page} (total {total})")
        if not (1 <= level <= 8):
            raise ValueError(f"TOC entry level invalid: {level}")
    doc.set_toc(toc)
    doc.saveIncr()
    doc.close()


def backup_path_for(pdf_path: str) -> str:
    stem, _ = os.path.splitext(pdf_path)
    return stem + ".toc.bak.json"


def backup_toc(pdf_path: str) -> str:
    sidecar = backup_path_for(pdf_path)
    if os.path.exists(sidecar):
        # 保留最早备份：第一次备份前的状态才是真正要保护的原始大纲。
        # 二次 --force-overwrite 时若覆盖，出版商原始大纲将永久丢失。
        logger.info("TOC backup already exists, keeping earliest: %s", sidecar)
        return sidecar
    payload = {
        "file": os.path.abspath(pdf_path),
        "backed_up_at": datetime.now(timezone.utc).isoformat(),
        "toc": get_toc(pdf_path),
    }
    with open(sidecar, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    logger.info("TOC backup written: %s (%d entries)", sidecar, len(payload["toc"]))
    return sidecar


def restore_toc(pdf_path: str, purge: bool = False) -> dict:
    sidecar = backup_path_for(pdf_path)
    with open(sidecar, encoding="utf-8") as f:
        payload = json.load(f)
    toc = payload.get("toc") or []
    set_toc(pdf_path, toc)
    if purge:
        os.remove(sidecar)
    return {"restored_entries": len(toc), "sidecar": sidecar, "purged": purge}
