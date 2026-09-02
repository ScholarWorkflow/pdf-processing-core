"""inject-toc: auto-build a navigable outline for PDFs that lack one.

Pipeline (Phase C application layer):
  1. flip through the first N pages via extract_pdf(strategy="fast")
  2. locate the TOC page run with line-shape heuristics (header word /
     short-line share / trailing page-number share)
  3. PRIMARY - LLM understanding (v3): rendered TOC pages go to a
     configured structured-vision chain (toc_llm) which reads the actual
     layout and returns strict JSON entries. Script-side gates
     (entry-count floor, printed-number share, monotonicity) decide which
     chunks are accepted; rejected pages fall through to the legacy path:
     transcribe candidate pages (text layer when clean, otherwise
     layout-aware OCR adapter on a 200dpi render - renders serialized on the
     main thread, HTTP posts concurrent) + rule-based line parser.
  5a. offset mapping via detect_page_segments; on a text-layer-less book the
      offset is probed by OCR-ing candidate pages printed+offset for
      offset = 0..PROBE_MAX_OFFSET and matching the entry's chapter pattern /
      title prefix (TOC pages themselves are excluded from candidates)
  5b. ANCHOR-SCAN FALLBACK (v2): when the TOC prints no usable page numbers,
      or the offset probe fails, entries are located directly - body pages
      are OCR-ed in order (native OCR, batched renders + parallel calls)
      and each entry is matched against its expected position under the
      document-order constraint. No printed->physical mapping is needed.
      Validated structurally: >=60% of level-1 chapters must be anchored.
  6. backup_toc -> set_toc (in-place incremental save)

Every failure before step 6 leaves the PDF byte-identical.

Exit codes: 3 no TOC candidate found, 4 parse rate < 60%, 5 sample pass
rate < 50%, 6 PDF already has an outline (use --force-overwrite), 7 offset
undeterminable AND anchor scan not applicable/failed on a text-layer-less
book, 8 anchor scan coverage insufficient (<60% level-1 chapters anchored),
1 generic input/environment errors.

Offset policy: detect_page_segments normally derives offsets from printed
page-number stubs in the text layer. Pure-scan books have none and the
detector falls back to offset=0 — NEVER trusted here. When the segments are
degenerate, the offset is probed per-entry (chapter heading / title prefix);
if that fails, the full anchor scan locates every entry physically and the
offset becomes irrelevant.
"""

from __future__ import annotations

import logging
import os
import random
import re
import shutil
import tempfile
import time
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from .extract import extract_pdf
from . import ocr_apple
from .quality import RE_HAN_COUNT, score_text
from . import ocr_docparser
from . import toc as toc_mod
from ._vlhttp import OCRError, render_page_png

logger = logging.getLogger(__name__)

TOC_HEADER_RE = re.compile(r"^(目\s*[次录錄]|contents\b|table\s+of\s+contents)", re.IGNORECASE)
SEPS_ANY = r"\s·•⋅‧‥…\u2026.\uFF0E\u3002_\-\u2013\u2014\u2015\u2212\u30FB"
SEPS_SINGLE_OK = r"\s·•⋅‧‥…\u2026\u3002_\-\u2013\u2014\u2015\u2212\u30FB"
TRAIL_PAGE_RE = re.compile(
    f"(?:[{SEPS_ANY}]{{2,}}|[{SEPS_SINGLE_OK}])([0-9]{{1,4}}|[ivxIVXlcdmLCDM]{{2,7}})$"
)
TRAIL_GLUE_RE = re.compile(r"(?<![0-9.\uFF0E])([0-9]{2,4}|[ivxlcdm]{2,7})$")
GLUE_MIN_HAN = 2
TRAIL_LOOSE_RE = re.compile(r"(?:[0-9]{1,4}|[ivxlcdmIVXLCDM]{2,8})\s*$")
CHAPTER_RE = re.compile(r"^第\s*(\d+|[一二三四五六七八九十百千两]+)\s*(章|篇)")
CHAPTER_EN_RE = re.compile(r"^chapter\s*\d+", re.IGNORECASE)
NUM_DOTTED_RE = re.compile(
    r"^(\d{1,3})(?:\.(\d{1,3}))?(?:\.(\d{1,3}))?(?:\.(\d{1,3}))?"
    r"(?=$|[\s.、\uFF0E,，;；:：)])"
)
LEADER_TAIL_RE = re.compile(f"[{SEPS_ANY}]+$")
LEADING_DECOR_RE = re.compile(r"^[\s·•⋅‧‥….\u3002\uFF0E○●◎□■▲△◆◇★☆※§¶†‡\-–—―]+")
CONTENT_CHAR_RE = re.compile(rf"{RE_HAN_COUNT.pattern}|[A-Za-z0-9]")

MIN_WINDOW_SCORE = 5.5
MIN_WINDOW_TRAILS = 3
TEXTLAYER_GARBLE_MAX = 0.05
TEXTLAYER_MIN_TRAILS = 5
PARSE_RATE_MIN = 0.6
SAMPLE_PASS_MIN = 0.5

# v2: offset probe + anchor scan
PROBE_MAX_OFFSET = 50          # candidate offsets tried in ascending order
ANCHOR_SCAN_DPI = 150          # native OCR flat-text render for body scan
ANCHOR_BATCH = 12              # pages rendered per batch (renders stay on main thread)
ANCHOR_PARALLEL = 4            # concurrent native OCR calls per batch
ANCHOR_LOOKAHEAD = 8           # eligible unmatched entries while scanning a page
ANCHOR_STRICT_PREFIX = 6       # normalized chars required for an exact hit
ANCHOR_FUZZY_MIN = 0.75        # char-set overlap floor for the fuzzy tier
ANCHOR_MIN_CHAPTER_COVER = 0.6 # structural validation gate
ANCHOR_MIN_ENTRIES = 5         # minimum entries worth anchor-scanning
STRONG_KINDS = ("strict", "numtok", "chapter")  # independent-evidence tiers
TOC_SHAPE_SKIP_SCORE = 3.0      # front-page score above this = strong TOC signature
WINDOW_CANDIDATES = 4           # top-K disjoint windows tried against the provider
MIN_WRITTEN_DIVISOR = 60        # degenerate gate: written < max(4, total//this) = refuse
DIVIDER_SEARCH_BACK = 4         # unnumbered divider sits within N pages before its next section
STRUCT_KEEP_MIN = 0.8           # structural conservation: written >= this share of accepted entries
STRUCT_HARD_MIN = 0.5           # below this share -> refuse to write (exit 4)

_CN_NUM = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八",
           9: "九", 10: "十", 11: "十一", 12: "十二", 13: "十三", 14: "十四",
           15: "十五", 16: "十六", 17: "十七", 18: "十八", 19: "十九", 20: "二十"}


class InjectTocError(Exception):
    def __init__(self, code: int, message: str, payload: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.payload = {"error": message, **(payload or {})}


def _norm_line(raw: str) -> str:
    line = unicodedata.normalize("NFKC", raw)
    return re.sub(r"\s+", " ", line).strip()


def _split_trailing_number(line: str) -> tuple[str, int | None]:
    m = TRAIL_PAGE_RE.search(line)
    if m:
        return line[: m.start()], m.group(1)
    m = TRAIL_GLUE_RE.search(line)
    if m and len(RE_HAN_COUNT.findall(line[: m.start()])) >= GLUE_MIN_HAN:
        return line[: m.start()], m.group(1)
    return line, None


def _printed_from_token(token: str) -> int | None:
    if token.isdigit():
        return int(token)
    v = toc_mod.roman_to_int(token.lower())
    return v if v and 1 <= v <= 5000 else None


def _detect_level(title: str) -> tuple[int, bool]:
    if CHAPTER_RE.match(title) or CHAPTER_EN_RE.match(title):
        return 1, False
    m = NUM_DOTTED_RE.match(title)
    if m:
        depth = sum(1 for g in m.groups() if g)
        return max(1, min(8, depth)), False
    return 2, True


NUMBERLESS_ENTRY_RE = re.compile(
    r"^(?:第\s*\d+\s*章|chapter\s*\d+|\d{1,2}(?:\.\d{1,2}){1,3}(?!\d)|习题\s*\d|复习题\s*\d|附录)", re.IGNORECASE)


def parse_transcript_line(raw: str, allow_numberless: bool = False) -> dict | None:
    line = _norm_line(raw)
    if not line or len(line) < 2:
        return None
    head, token = _split_trailing_number(line)
    if token is None:
        if not allow_numberless:
            return None
        # 无页码条目（转录丢右列的目录页）：标题本身要长得像条目才收
        cand = LEADER_TAIL_RE.sub("", line).strip()
        cand = LEADING_DECOR_RE.sub("", cand).strip()
        if not NUMBERLESS_ENTRY_RE.match(cand):
            return None
        level, low = _detect_level(cand)
        return {"level": level, "title": cand, "printed": None,
                "low_confidence": True, "_raw": raw.strip()}
    printed = _printed_from_token(token)
    if printed is None:
        return None
    head = LEADER_TAIL_RE.sub("", head).strip()
    head = LEADING_DECOR_RE.sub("", head).strip()
    if not head or not CONTENT_CHAR_RE.search(head):
        return None
    level, low = _detect_level(head)
    return {
        "level": level,
        "title": head,
        "printed": printed,
        "low_confidence": low,
        "_raw": raw.strip(),
    }


def _recover_missing_numbers(doc, phys: int, entries: list[dict],
                             max_num: int | None = None) -> list[dict]:
    """目录页转录丢了行尾页码（Doc-Parser 在密集目录页会丢右列）时的补救：
    裁右侧页码条带（3x）用 Apple Vision 单独读出数字序列，按阅读顺序配回条目。

    配对规则：条目数 ≥ 数字数时允许丢弃前 k 条无编号条目（前言等）对齐；
    数字序列经 LDS 降噪后必须单调不减，否则放弃。多页目录时传 max_num
    （下一张目录页的首个页码）可滤掉混入的更大数字。失败时原样返回。
    """
    import pymupdf as _fitz

    nums = []
    try:
        page = doc[phys - 1]
        W, H = page.rect.width, page.rect.height
        clip = _fitz.Rect(W * 0.78, H * 0.14, W, H * 0.92)
        pix = page.get_pixmap(matrix=_fitz.Matrix(3, 3), clip=clip)
        from . import ocr_apple

        txt = ocr_apple.ocr_png(pix.tobytes("png"))
        nums = [int(l.strip()) for l in txt.splitlines() if re.fullmatch(r"\d{1,3}", l.strip())]
    except Exception as e:
        logger.warning("strip OCR failed on physical page %d: %s", phys, e)
        return entries
    if not nums:
        return entries
    if max_num is not None:
        filtered = [n_ for n_ in nums if n_ < max_num]
        if len(filtered) >= 3:
            nums = filtered
            logger.info("strip bound p%d: kept %d numbers < %d", phys, len(nums), max_num)
    # 条带 OCR 会混入杂讯（页眉数字、误读）——保留最长非降子序列，
    # 只要丢弃的不超过 30% 就继续配对，不再因个别乱序整体放弃
    keep = _longest_nondecreasing(nums)
    if len(keep) < 3 or len(keep) < len(nums) * 0.7:
        logger.info("strip numbers too noisy on p%d (%d/%d clean), skip pairing",
                    phys, len(keep), len(nums))
        return entries
    nums = [nums[i] for i in keep]
    mono = all(b >= a for a, b in zip(nums, nums[1:]))

    deficit = len(entries) - len(nums)
    if deficit < 0 or deficit > 8:
        logger.info("strip pairing infeasible on p%d: %d entries vs %d numbers", phys, len(entries), len(nums))
        return entries
    start = len(entries) - len(nums)
    for k in range(start, -1, -1):
        cand = entries[k:]
        if len(cand) != len(nums):
            continue
        ok = all(0 < n <= 999 for n in nums)
        if ok:
            for e, n in zip(cand, nums):
                e["printed"] = n
            logger.info("p%d: recovered %d page numbers via strip pairing (dropped %d leading)",
                        phys, len(nums), k)
            return entries
    return entries


def _longest_nondecreasing(arr: list[int]) -> list[int]:
    """Indices of the longest non-decreasing subsequence (O(n^2), n is small)."""
    if not arr:
        return []
    n = len(arr)
    best_len = [1] * n
    prev = [-1] * n
    for i in range(n):
        for j in range(i):
            if arr[j] <= arr[i] and best_len[j] + 1 > best_len[i]:
                best_len[i] = best_len[j] + 1
                prev[i] = j
    k = max(range(n), key=lambda i: best_len[i])
    out = []
    while k != -1:
        out.append(k)
        k = prev[k]
    return out[::-1]


def trim_repetition(text: str, max_lines: int = 250) -> str:
    """VL 转录的生成循环压制（两层）：

    a) 连续完全相同的行只保留 2 行；
    b) 周期性循环（一串不同行整体复读）：首次遇到已见过的行即截断——
       目录页里标题几乎不会合法重复，误截的代价远小于循环污染。
    截断后仍超 max_lines 再按头部截断。
    """
    lines = text.split("\n")
    out = []
    seen = set()
    for ln in lines:
        if len(out) >= 2 and out[-1] == ln and out[-2] == ln:
            continue
        if ln.strip() and ln in seen:
            logger.info("transcript cycle cut at line %d (repeated %r)", len(out), ln[:30])
            break
        seen.add(ln)
        out.append(ln)
        if len(out) >= max_lines * 3:
            break
    return "\n".join(out[:max_lines])


def parse_transcript(text: str, allow_numberless: bool = False) -> tuple[list[dict], int]:
    entries, entry_like = [], 0
    for raw in (text or "").splitlines():
        line = _norm_line(raw)
        if len(line) < 2:
            continue
        head, token = _split_trailing_number(line)
        if token is not None:
            entry_like += 1
        elif allow_numberless and NUMBERLESS_ENTRY_RE.search(line):
            entry_like += 1
        ent = parse_transcript_line(raw, allow_numberless=allow_numberless)
        if ent:
            entries.append(ent)
    return entries, entry_like


def _page_shape(text: str) -> dict:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return {"score": 0.0, "trails": 0, "lines": 0, "header": False, "first_line": ""}
    n = len(lines)
    header = bool(TOC_HEADER_RE.match(lines[0]))
    trails = sum(1 for ln in lines if TRAIL_LOOSE_RE.search(ln))
    shorts = sum(1 for ln in lines if len(ln) <= 45)
    score = (4.0 if header else 0.0) + 3.0 * trails / n + 1.5 * shorts / n
    return {
        "score": round(score, 3),
        "trails": trails,
        "lines": n,
        "header": header,
        "first_line": lines[0][:40],
    }


def rank_toc_windows(front_shapes: list[dict], max_pages: int, k: int = 4
                     ) -> list[tuple[list[int], float]]:
    """Top-K disjoint TOC-page windows, best first (v3).

    Why K: the single-best window is chosen by line-shape heuristics, which
    formula-dense body pages (每行以数字收尾的要点页) can beat other candidates.
    A real TOC can be incorrectly outranked by body pages. Rules
     propose candidates; the LLM stage disposes.
    Returns [(physical_pages_1based, score)], disjoint, score-descending.
    """
    n = len(front_shapes)
    cands: list[tuple[float, int, int]] = []
    for length in range(1, max_pages + 1):
        for start in range(0, n - length + 1):
            win = front_shapes[start : start + length]
            total = sum(w["score"] for w in win)
            trails = sum(w["trails"] for w in win)
            if trails < MIN_WINDOW_TRAILS:
                continue
            cands.append((total, start, length))
    cands.sort(key=lambda t: (-t[0], t[2], t[1]))
    picked: list[tuple[int, int]] = []
    for total, start, length in cands:
        if len(picked) >= k:
            break
        if any(start < s + l and s < start + length for s, l in picked):
            continue
        # 弱边缘裁剪：与旧 select_toc_pages 相同的收尾逻辑
        idx = list(range(start, start + length))
        while len(idx) > 1:
            edge_scores = [front_shapes[i]["score"] for i in (idx[0], idx[-1])]
            if max(edge_scores) < 1.0:
                idx.pop(edge_scores.index(min(edge_scores)))
            else:
                break
        picked.append((idx[0], len(idx)))
    out = []
    for start, length in picked:
        pages = [start + i + 1 for i in range(length)]  # shapes[i] = physical i+1
        score = round(sum(front_shapes[p - 1]["score"] for p in pages), 3)
        out.append((pages, score))
    return out


def _transcribe_pages(doc, phys_pages: list[int], dpi: int) -> list[tuple[str, str, dict]]:
    rendered = [(p, render_page_png(doc[p - 1], dpi=dpi)) for p in phys_pages]
    results: dict[int, tuple[str, str, dict]] = {}

    def work(phys: int, png: bytes) -> tuple[str, str, dict]:
        text = ocr_docparser.ocr_png(png)
        return text, "doc-parser", {}

    pending = [(p, png) for p, png in rendered]
    textlayer = []
    for phys, png in pending:
        page = doc[phys - 1]
        raw_norm = unicodedata.normalize("NFC", page.get_text() or "")
        g, _, _ = score_text(raw_norm)
        lines = [ln.strip() for ln in raw_norm.splitlines() if ln.strip()]
        tl = sum(1 for ln in lines if TRAIL_LOOSE_RE.search(ln))
        if g < TEXTLAYER_GARBLE_MAX and tl >= TEXTLAYER_MIN_TRAILS:
            results[phys] = (raw_norm, "textlayer", {"garble": round(g, 4), "trail_lines": tl})
            textlayer.append(phys)
    pending = [(p, png) for p, png in pending if p not in results]

    if pending:
        logger.info("doc-parser transcribing %d page(s): %s", len(pending), [p for p, _ in pending])
        with ThreadPoolExecutor(max_workers=min(4, len(pending))) as ex:
            futs = {ex.submit(work, p, png): p for p, png in pending}
            done = 0
            for fut in as_completed(futs):
                phys = futs[fut]
                done += 1
                try:
                    results[phys] = fut.result()
                    results[phys][2].update({"png_bytes": len(dict(pending)[phys])})
                except OCRError as e:
                    logger.error("doc-parser failed on physical page %d: %s", phys, e)
                    raise InjectTocError(1, f"OCR failed on physical page {phys}: {e}")
                logger.info("transcribed physical page %d (%d/%d)", phys, done, len(pending))

    return [results[p] for p in phys_pages]


def _own_printed(phys: int, segments: list) -> int | None:
    for seg in segments:
        if seg["start"] <= phys <= seg["end"]:
            return phys - seg["offset"]
    return None


def _is_degenerate_segments(segments: list, total: int) -> bool:
    return len(segments) == 1 and segments[0] == {"start": 1, "end": total, "offset": 0}


def _chapter_number(title: str) -> int | None:
    m = re.search(r"第\s*(\d+)\s*章", title)
    if m:
        return int(m.group(1))
    m = re.search(r"第\s*([一二三四五六七八九十]+)\s*章", title)
    if m:
        rev = {v: k for k, v in _CN_NUM.items()}
        return rev.get(m.group(1).replace("两", "二"))
    m = re.match(r"chapter\s*(\d+)", title, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None


def _probe_offset_by_anchor(doc, total: int, probe_entries: list[dict],
                            toc_pages: list[int], shapes: list[dict]) -> tuple[int | None, dict]:
    """Probe the printed->physical offset using the earliest TOC entries.

    Offsets 0..PROBE_MAX_OFFSET are tried in ascending order (small front
    matter is the common case). At each offset every entry votes by OCR-ing
    its candidate page `printed + offset` and demanding a STRONG match
    (strict prefix / equal-number chapter heading / number token + han-core
    overlap). An offset wins when >=2 distinct entries agree on it - single
    equation-reference collisions ((2.2.3) citing its own section) cannot
    manufacture that agreement; a lone hit is only accepted after the whole
    range yields no pair. OCR results are cached per page, so each page is
    read at most once across all entries and offsets. The TOC pages
    themselves are skipped - entries' own lines inside the TOC would
    otherwise produce false hits (body pages adjacent to the TOC stay in the
    candidate pool; v2.1 learned this when 第1章 started on the very page
    after the last TOC page). v1 reused the printed page as the chapter
    number and matched verbatim prefixes that 简/繁/日 variant misreads
    (变↔変) broke; both flaws are gone.
    """
    def _toc_like(phys: int) -> bool:
        # 只跳「强目录特征」页：窗口偶尔吞进正文首页（弱分），那正是
        # 第1章的起点，跳了它探针就永远找不到 offset（v2.1 实测教训）
        return 1 <= phys <= len(shapes) and shapes[phys - 1]["score"] >= TOC_SHAPE_SKIP_SCORE

    flat_cache: dict[int, str] = {}

    def flat_of(phys: int) -> str:
        # 快引擎（AV ~1s/页；VL 全页 ~30s）。正确性由双条目表决 +
        # 抽验的 VL 独立证据把关，这里只求把候选页快速过一遍
        if phys not in flat_cache:
            try:
                txt = ocr_apple.ocr_page(doc[phys - 1], dpi=150)
            except Exception as e:
                logger.warning("probe ocr failed on physical page %d: %s", phys, e)
                txt = ""
            flat_cache[phys] = unicodedata.normalize("NFC", txt or "")
        return flat_cache[phys]

    probed: set[int] = set()
    singles: list[tuple[int, dict, str]] = []
    if not probe_entries:
        return None, {"note": "no probe entries"}
    min_printed = min(e["printed"] for e in probe_entries)
    for off in range(0, PROBE_MAX_OFFSET + 1):
        # 终止条件只看「最早条目也已越过书尾」——某个 offset 的候选恰好
        # 全落在被跳过的目录页上是正常的（v2.1 曾因此提前 break，探针
        # 扫了 6 页就停，永远到不了真正的第1章起始页）
        if min_printed + off > total:
            break
        hits_here: list[tuple[int, dict, str]] = []
        for ent in probe_entries:
            phys = ent["printed"] + off
            if phys > total or _toc_like(phys) or phys < 1:
                continue
            probed.add(phys)
            kind = _match_title(ent["title"], flat_of(phys), require_strong=True)
            if kind:
                hits_here.append((off, ent, kind))
        singles.extend(hits_here)
        if len({e["printed"] for _, e, _ in hits_here}) >= 2:
            titles = [f"{e['title'][:20]}@p{e['printed'] + off}" for _, e, _ in hits_here]
            logger.info("offset probe agreement at offset %d (%s)", off, "; ".join(titles))
            return off, {"agreed_entries": [{"title": e["title"], "printed": e["printed"],
                                             "kind": k} for _, e, k in hits_here],
                         "applied_offset": off, "probed_physicals": sorted(probed)}
    if singles:
        off, ent, kind = singles[0]
        logger.info("offset probe single-hit fallback: %r (printed %d) at physical %d "
                    "-> offset %d (%s)", ent["title"][:30], ent["printed"],
                    ent["printed"] + off, off, kind)
        return off, {"agreed_entries": [{"title": ent["title"], "printed": ent["printed"],
                                         "kind": kind}],
                     "applied_offset": off,
                     "note": "single-entry agreement only (no pair found)",
                     "probed_physicals": sorted(probed)}
    return None, {"probed_physicals": sorted(probed),
                  "note": f"no strong hit within offset 0..{PROBE_MAX_OFFSET} "
                          f"(skipped TOC-like pages), tried {len(probe_entries)} entries"}


# v3: 标点不敏感归一（全角/半角、。与.、・与· 等在 OCR/转录间随机漂移，
# 目录匹配必须先抹平这类差异才谈得上语义比对）
_MATCH_PUNCT_TRANS = str.maketrans({
    "。": ".", "、": ",", "，": ",", "：": ":", "；": ";", "！": "!", "？": "?",
    "（": "(", "）": ")", "【": "[", "】": "]", "「": '"', "」": '"',
    "『": '"', "』": '"', "　": "", "•": "·", "‧": "·", "⋅": "·", "‥": "..", "…": "...",
})


def _norm_for_match(s: str) -> str:
    s = unicodedata.normalize("NFC", s)
    s = re.sub(r"\s+", "", s)
    return s.translate(_MATCH_PUNCT_TRANS)


def _match_title(title: str, raw_text, require_strong: bool = False) -> str | None:
    """Match an entry title against one page's raw OCR/text-layer content.

    raw_text: raw multi-line string (whitespace inside is normalized away).
    Tiers (strongest first):
      strict  - exact normalized prefix anywhere
      chapter - a 第N章/chapter-N token WITH EQUAL NUMBER appears and the
                title's han core overlaps the page (number equality keeps
                other chapters' running heads out)
      numtok  - the entry's dotted number token appears (dot-loss tolerated)
                and >=70% of the following han-core chars exist on the page;
                char-set comparison survives 简/繁/日 variant misreads (变↔変)
      fuzzy   - unnumbered titles only, char-set overlap >= ANCHOR_FUZZY_MIN;
                never accepted as evidence when require_strong=True

    Page-level (not line-level) because real OCR merges headers/headings into
    one line; residual equation-reference collisions ((2.2.3) citing the
    section it discusses) are filtered by requiring TWO entries to agree on
    the same offset during probing (_probe_offset_by_anchor).
    """
    n = _norm_for_match(title)
    if not n or not raw_text:
        return None
    raw = unicodedata.normalize("NFC", raw_text)
    lines = [_norm_for_match(ln) for ln in raw.splitlines() if ln.strip()]
    flat = _norm_for_match(raw)

    def han_core(s: str, k: int) -> str:
        return re.sub(r"[0-9.\s^_{}$~·•]+", "", s)[:k]

    if n[:ANCHOR_STRICT_PREFIX] in flat:
        return "strict"

    chap_no = _chapter_number(n)
    m_tok = re.match(r"(\d{1,3}(?:\.\d{1,3}){0,3})", n)

    if chap_no is not None:
        for ln in lines:
            lm = re.search(r"第\s*(\d+)\s*章", ln) or re.search(r"chapter\s*(\d+)", ln, re.IGNORECASE)
            if lm and int(lm.group(1)) == chap_no:
                core = han_core(n, 10)
                if len(core) >= 4 and \
                        sum(c in ln for c in core) / len(core) >= 0.5:
                    return "chapter"
        return None

    if m_tok:
        tok, dig = m_tok.group(1), m_tok.group(1).replace(".", "")
        core = han_core(n, 10)
        if len(core) < 4:
            return None
        PUNCT_BEFORE = "（(【[]、，,．。：:；;"
        occ = []  # (start_in_flat, end_in_flat)
        for mm in re.finditer(re.escape(tok), flat):
            occ.append((mm.start(), mm.end()))
        if dig != tok:
            # 点号丢失的 OCR 变体：在去点串里找数字序列，再映射回 flat 下标
            kept_idx = [ci for ci, ch in enumerate(flat) if ch != "."]
            stripped = "".join(flat[ci] for ci in kept_idx)
            for mm in re.finditer(re.escape(dig), stripped):
                s_flat = kept_idx[mm.start()]
                e_flat = kept_idx[mm.end() - 1] + 1
                occ.append((s_flat, e_flat))
        for s, e in occ:
            # 公式引用形如 （2.2.3）：左邻是括号/标点的一律不算标题命中
            if s > 0 and flat[s - 1] in PUNCT_BEFORE:
                continue
            window = re.sub(r"[0-9.\s]+", "", flat[e: e + 18])
            win_ov = sum(c in window for c in core) / len(core)
            page_ov = sum(c in flat for c in core) / len(core)
            if win_ov >= 0.55 or page_ov >= 0.85:
                return "numtok"
        return None

    core = han_core(n, 12)
    if len(core) >= 6 and sum(c in flat for c in core) / len(core) >= ANCHOR_FUZZY_MIN:
        return None if require_strong else "fuzzy"
    return None


def _anchor_scan(doc, entries: list[dict], scan_start: int) -> dict:
    """Locate entries physically by scanning body pages in order (v2).

    One sequential pass: render ANCHOR_BATCH pages at a time (main thread -
    PyMuPDF is not thread-safe), OCR them with Apple Vision in parallel,
    then match ordered unmatched entries under the document-order constraint
    (entry k may only land on a page >= entry k-1's page). First match wins;
    skipped-over entries stay unanchored and get dropped later.
    """
    import concurrent.futures as cf

    total = len(doc)
    cursor = 0
    pages_scanned = 0
    t0 = time.time()
    for batch_start in range(scan_start, total + 1, ANCHOR_BATCH):
        if cursor >= len(entries):
            break
        batch = range(batch_start, min(batch_start + ANCHOR_BATCH, total + 1))
        pngs = [(p, render_page_png(doc[p - 1], dpi=ANCHOR_SCAN_DPI)) for p in batch]

        def work(png: bytes) -> str:
            # 兜底通道保持轻量（AV）；结构校验负责正确性。
            from . import ocr_apple

            try:
                return ocr_apple.ocr_png(png)
            except Exception:
                return ""

        with cf.ThreadPoolExecutor(max_workers=ANCHOR_PARALLEL) as ex:
            texts = list(ex.map(work, [png for _, png in pngs]))
        for (phys, _), txt in zip(pngs, texts):
            pages_scanned += 1
            if cursor >= len(entries):
                break
            if not (txt or "").strip():
                continue
            for k in range(cursor, min(cursor + ANCHOR_LOOKAHEAD, len(entries))):
                kind = _match_title(entries[k]["title"], txt)
                if kind:
                    entries[k]["physical"] = phys
                    entries[k]["anchor_kind"] = kind
                    logger.info("anchor: [%d] %r -> p%d (%s)",
                                k, entries[k]["title"][:30], phys, kind)
                    cursor = k + 1
                    break
        if batch_start % (ANCHOR_BATCH * 4) == 0 or cursor >= len(entries):
            logger.info("anchor scan: %d/%d entries anchored (%d pages, %.0fs)",
                        sum(1 for e in entries if e.get("physical")), len(entries),
                        pages_scanned, time.time() - t0)

    matched = [e for e in entries if e.get("physical")]
    l1 = [e for e in entries if e["level"] == 1]
    l1_hit = [e for e in l1 if e.get("physical")]
    return {
        "entries_total": len(entries),
        "entries_anchored": len(matched),
        "chapters_total": len(l1),
        "chapters_anchored": len(l1_hit),
        "pages_scanned": pages_scanned,
        "elapsed_s": round(time.time() - t0, 1),
        "unmatched_titles": [e["title"] for e in entries if not e.get("physical")][:20],
    }


def _anchor_validate(entries: list[dict], scan_report: dict) -> None:
    """Structural gate for anchor mode: enough chapters anchored, strictly
    increasing physical pages among written entries."""
    l1_total = scan_report["chapters_total"]
    l1_hit = scan_report["chapters_anchored"]
    need = max(1, int(l1_total * ANCHOR_MIN_CHAPTER_COVER + 0.999))
    if l1_total == 0 and len(entries) < ANCHOR_MIN_ENTRIES:
        raise InjectTocError(8, "anchor scan found too few entries to build an outline",
                             {"scan": scan_report})
    if l1_total > 0 and l1_hit < need:
        raise InjectTocError(
            8, f"anchor scan coverage insufficient: {l1_hit}/{l1_total} level-1 chapters "
               f"anchored (< {int(ANCHOR_MIN_CHAPTER_COVER * 100)}%)",
            {"scan": scan_report})
    written = [e for e in entries if e.get("physical")]
    phys_list = [e["physical"] for e in written]
    bad = [(a, b) for a, b in zip(phys_list, phys_list[1:]) if b < a]
    if bad:
        raise InjectTocError(8, f"anchor scan produced non-monotonic pages: {bad[:3]}",
                             {"scan": scan_report})


def _sample_check(entries: list[dict], pdf_path: str, segments: list, k: int,
                  require_independent: bool = False) -> dict:
    import pymupdf as fitz

    picked = random.sample(entries, min(k, len(entries))) if entries else []
    details, passed = [], 0
    doc = fitz.open(pdf_path)
    for ent in picked:
        phys = ent["physical"]
        page = doc[phys - 1] if 1 <= phys <= len(doc) else None
        page_text = (page.get_text() or "") if page is not None else ""
        if not page_text.strip() and page is not None:
            # 文字层没有内容时用 OCR 在目标页上找——分段自洽不算证据。
            # 引擎必须与标题转录一致（VL），否则变体字互相对不上（v2.1 教训）
            from ._vlhttp import render_page_png
            from . import toc_layout

            try:
                page_text = toc_layout.ocr_png_vl_first(render_page_png(page, dpi=150)) or ""
            except Exception as e:
                logger.warning("sample ocr failed on physical page %d: %s", phys, e)
                page_text = ""
        kind = _match_title(ent["title"], page_text,
                            require_strong=require_independent)
        title_found = kind is not None
        own = _own_printed(phys, segments)
        seg_match = own is not None and own == ent["printed"]
        ok = title_found if require_independent else (title_found or seg_match)
        passed += ok
        details.append({
            "title": ent["title"],
            "printed": ent["printed"],
            "physical_page": phys,
            "passed": ok,
            "match_kind": kind,
            "segment_printed_match": seg_match,
            "page_chars": len(page_text.strip()),
        })
    doc.close()
    return {
        "pass": passed,
        "fail": len(details) - passed,
        "details": details,
        "pass_rate": round(passed / len(details), 3) if details else 0.0,
    }


def _attach_unnumbered(doc, ordered_entries: list[dict], toc_like_pages: set[int],
                       shapes: list[dict]) -> tuple[list[dict], list[dict]]:
    """v3.1: locate null-printed entries (章扉/part dividers) instead of dropping.

    Why: offset mode can only place entries that carry a printed page number.
    Chapter-divider entries (講義N / Part N, printed_page=null in the TOC)
    used to be silently discarded here - which destroyed the outline's top
    level (dense TOC layouts can drop dividers and collapse sections
    under one promoted fake chapter). Dividers sit physically just before
    their first child section, so each one's search window is bracketed by
    the previously placed entry and the next numbered entry; candidates are
    verified with Apple Vision + STRONG title match (divider pages print the
    title big - strict tier hits reliably).

    Returns (attached_entries, failures) - failures carry their search
    windows for diagnostics.
    """
    total = len(doc)
    av_cache: dict[int, str] = {}

    def flat_of(phys: int) -> str:
        if phys not in av_cache:
            try:
                from ._vlhttp import render_page_png

                txt = ocr_apple.ocr_png(render_page_png(doc[phys - 1], dpi=150))
            except Exception as e:  # noqa: BLE001
                logger.warning("attach: av ocr failed on p%d: %s", phys, e)
                txt = ""
            av_cache[phys] = unicodedata.normalize("NFC", txt or "")
        return av_cache[phys]

    def _toc_like(phys: int) -> bool:
        if phys in toc_like_pages:
            return True
        return 1 <= phys <= len(shapes) and shapes[phys - 1]["score"] >= TOC_SHAPE_SKIP_SCORE

    def _divider_hit(title: str, phys: int) -> str | None:
        """扉页专用验证：编号相等 + 页级核心字重叠。

        为什么不用 _match_title 的 chapter 层：扉页常把「講義5」与大标题排成
        不同行/不同字号，AV 转录会把行拆碎；且 χ² 这类字形易被读歪，strict
        前缀一碰即碎。这里只要求 ①页面上出现同号章标记 ②标题核心字过半在页上。
        """
        n = _norm_for_match(title)
        m = re.match(r"(?:講義|第)\s*(\d+)", n)
        if not m:
            return None
        chap_no = int(m.group(1))
        if not any(re.search(rf"講義\s*{chap_no}(?!\d)|第\s*{chap_no}\s*[章講]", ln)
                   for ln in re.split(r"[\n.。]", flat_of(phys))):
            return None
        core = re.sub(r"[0-9.\s^_{}$~·•()\[\],،、]+", "", n)
        core = re.sub(r"^(?:講義|第)\d+[章講]?", "", core)[:12]
        if len(core) < 3:
            return "num-only"
        ov = sum(c in flat_of(phys) for c in core) / len(core)
        return "divider" if ov >= 0.4 else None

    attached: list[dict] = []
    failures: list[dict] = []
    last_phys = 0
    for idx, ent in enumerate(ordered_entries):
        if ent.get("physical") is not None:
            last_phys = max(last_phys, ent["physical"])
            continue
        nxt = next((e2["physical"] for e2 in ordered_entries[idx + 1:]
                    if e2.get("physical")), None)
        lo = last_phys + 1
        hi = (nxt - 1) if nxt else min(lo + DIVIDER_SEARCH_BACK, total)
        cands = [p for p in range(max(lo, hi - DIVIDER_SEARCH_BACK + 1), hi + 1)
                 if 1 <= p <= total and not _toc_like(p)]
        placed = False
        for p in sorted(cands, reverse=True):   # 离下一节最近的扉页优先
            # LLM 对同款标题有时带「講義N」前缀有时不带；裸标题再试一次，
            # 扉页装饰字被 OCR 读歪时常能靠裸标题的 strict 命中（v6 实测）
            alt_title = re.sub(r"^(?:講義|第)\s*\d+\s*[章講]?\s*", "",
                               _norm_for_match(ent["title"]))
            kind = _match_title(ent["title"], flat_of(p), require_strong=True) \
                or _divider_hit(ent["title"], p) \
                or (alt_title != _norm_for_match(ent["title"])
                    and _match_title(alt_title, flat_of(p), require_strong=True)
                    and "strict-alt") or None
            if kind:
                ent["physical"] = p
                ent["_num_src"] = "neighbor-anchor"
                ent["_anchor_kind"] = kind
                attached.append(ent)
                last_phys = p
                placed = True
                logger.info("attach: %r -> p%d (%s)", ent["title"][:30], p, kind)
                break
        if not placed and nxt is not None and 1 <= nxt <= total \
                and nxt > last_phys and not _toc_like(nxt):
            # 同页钉扎：无独立扉页的标题（如「数表」印在第一张表页上）——
            # 窗口为空或全灭时，允许指向下一节自身页，但要有最低字符证据
            core = re.sub(r"[^\u3040-\u9fffA-Za-z0-9]", "",
                          _norm_for_match(ent["title"]))[:12]
            if core and sum(c in flat_of(nxt) for c in core) / len(core) >= 0.3:
                ent["physical"] = nxt
                ent["_num_src"] = "neighbor-anchor"
                ent["_anchor_kind"] = "same-page"
                attached.append(ent)
                last_phys = nxt
                placed = True
                logger.info("attach(same-page): %r -> p%d", ent["title"][:30], nxt)
        if not placed:
            failures.append({"title": ent["title"], "window": [lo, hi],
                             "note": "no strong match in window"})
            logger.info("attach failed: %r window [%d,%d]",
                        ent["title"][:30], lo, hi)
    return attached, failures


def _sanitize_outline(outline: list[list]) -> list[list]:
    """pymupdf set_toc contract: first entry must be level 1, and depth may
    only grow by one step at a time. Books whose first parsed TOC entry is a
    mid-level section (earlier TOC pages lost to OCR) would otherwise crash
    at write time; demote the head entry to level 1 and clamp jumps."""
    out = []
    prev = 0
    for lv, title, page in outline:
        eff = 1 if not out else max(1, min(lv, prev + 1))
        out.append([eff, title, page])
        prev = eff
    return out


def _is_chapter_title(title: str) -> bool:
    t = re.sub(r"\s+", "", title)
    return bool(CHAPTER_RE.match(t) or re.match(r"^\d{1,2}章", t)
                or CHAPTER_EN_RE.match(t))


def run(
    pdf_path: str,
    front_pages: int = 30,
    max_toc_pages: int = 8,
    sample_check: int = 5,
    dpi: int = 200,
    force_overwrite: bool = False,
    force_anchor: bool = False,
    no_llm: bool = False,
) -> dict:
    t0 = time.time()
    pdf_path = os.path.abspath(pdf_path)
    if not os.path.exists(pdf_path):
        raise InjectTocError(1, f"file not found: {pdf_path}")

    import pymupdf as fitz

    doc = fitz.open(pdf_path)
    total = len(doc)
    logger.info("inject-toc: %s (%d pages)", pdf_path, total)

    existing = doc.get_toc()
    if existing and not force_overwrite:
        raise InjectTocError(
            6, f"PDF already has an outline ({len(existing)} entries); "
               f"refusing to overwrite. Pass --force-overwrite to replace it.",
            {"existing_entries": len(existing)})

    logger.info("[step 1/6] front scan: first %d page(s), strategy=fast", front_pages)
    n_front = min(front_pages, total)
    tmpdir = tempfile.mkdtemp(prefix="pdfx_inject_")
    try:
        nd = fitz.open()
        nd.insert_pdf(doc, from_page=0, to_page=n_front - 1)
        tmp_pdf = os.path.join(tmpdir, "front.pdf")
        nd.save(tmp_pdf)
        nd.close()
        front_res = extract_pdf(tmp_pdf, strategy="fast")
        front_texts = [pt["text"] for pt in front_res["page_texts"]]
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    shapes = [_page_shape(t) for t in front_texts]
    for i, sh in enumerate(shapes, 1):
        logger.info("front p%d score=%s trails=%d/%d header=%s first=%r",
                    i, sh["score"], sh["trails"], sh["lines"], sh["header"], sh["first_line"])

    logger.info("[step 2/6] locating TOC candidate windows (top-%d)", WINDOW_CANDIDATES)
    windows = rank_toc_windows(shapes, max_toc_pages, k=WINDOW_CANDIDATES)
    if not windows:
        raise InjectTocError(3, "no TOC candidate page found in front pages",
                             {"front_first_lines": [
                                 {"page": i, "first_line": shapes[i - 1]["first_line"]}
                                 for i in range(1, n_front + 1)]})
    render_cache: dict[int, bytes] = {}

    def _png_of(p: int) -> bytes:
        if p not in render_cache:
            from ._vlhttp import render_page_png as _render

            render_cache[p] = _render(doc[p - 1], dpi=dpi)
        return render_cache[p]

    logger.info("ranked windows: %s", [(w[0], w[1]) for w in windows])

    # v3 主路：目录理解交给结构化视觉提供方（复杂排版由提供方处理）。
    # 候选窗口按得分排序逐个交给提供方验证——行尾数字启发式会被公式密集的
    # Body pages can outrank the real TOC; retain independent layout evidence.
    # 所以由「提供方能否读出合格条目」决定用哪个窗口；校验不过的页再回落旧管线。
    llm_entries: dict[int, list[dict]] = {}
    llm_meta: dict = {}
    toc_phys, window_score = windows[0]
    tried_windows: list[dict] = []
    if not no_llm:
        try:
            from . import toc_llm

            for cand_phys, cand_score in windows:
                pngs = [(p, _png_of(p)) for p in cand_phys]
                ents_c, meta_c = toc_llm.parse_toc_pages(pngs, total)
                tried_windows.append({"pages": cand_phys, "score": cand_score,
                                      "accepted_pages": sorted(ents_c),
                                      **meta_c})
                if ents_c:
                    llm_entries, llm_meta = ents_c, meta_c
                    toc_phys, window_score = cand_phys, cand_score
                    break
            if llm_entries:
                logger.info("structured vision accepted %d/%d TOC page(s) on window %s, %d entries",
                            len(llm_entries), len(toc_phys), toc_phys,
                            llm_meta["entries"])
            else:
                logger.info("structured vision accepted no window (%d tried); "
                            "full fallback to legacy parsing on best-score window",
                            len(tried_windows))
        except Exception as e:  # noqa: BLE001 - env/key issues must not kill inject
            logger.warning("structured TOC path failed entirely (%s); legacy path only", e)

    # 首选：布局提供方框定内容区 + 投影切行（几何缩进=文档自己的层级证据，
    # 整行裁条 OCR 把标题/点线/页码一起读回来）；失败页回落整页布局适配器转录。
    # 结构化视觉提供方接管窗口后：被它拒绝的页=判定为非目录页（正文/空白），信任该判断、
    # 不再喂给规则解析器制造垃圾；只有结构化路径整体缺席时才全窗口走旧管线。
    layout_entries: dict[int, list[dict]] = {}
    layout_pages = [] if llm_entries else list(toc_phys)
    if layout_pages:
        try:
            from . import toc_layout

            kept: list[tuple[int, list[dict]]] = []
            all_rows: list[list[dict]] = []
            for p in layout_pages:
                try:
                    rows, _meta = toc_layout.extract_toc_rows(doc[p - 1], dpi=dpi)
                    kept.append((p, rows))
                    all_rows.append(rows)
                except Exception as e:  # noqa: BLE001
                    logger.info("layout path unavailable on physical p%d: %s", p, e)
            toc_layout.assign_levels(all_rows)
            all_pairs: list[tuple[dict, dict]] = []
            for p, rows in kept:
                texts, rec_nums = toc_layout.ocr_rows(rows)
                ents = []
                for r, txt, rec in zip(rows, texts, rec_nums):
                    line = " ".join(txt.split())
                    if not line:
                        continue
                    ent = parse_transcript_line(line) or \
                        parse_transcript_line(line, allow_numberless=True)
                    if ent is None:
                        continue  # 有右缘页码也没有可用标题，放弃该行
                    # 垃圾行门槛：裁条偶尔切掉左边界留下碎片（如「題 1.3」）——
                    # 连续汉字不足两个、又没有点号编号/章模式的行不配当条目
                    if not (re.search(r"[\u4e00-\u9fff]{2,}", ent["title"])
                            or NUM_DOTTED_RE.match(ent["title"])
                            or CHAPTER_RE.match(ent["title"]) or CHAPTER_EN_RE.match(ent["title"])):
                        logger.info("drop fragment row %r", ent["title"][:24])
                        continue
                    ent["_rec_num"] = rec
                    ent["_raw_line"] = line
                    all_pairs.append((r, ent))
                    ents.append(ent)
                # 右缘页码配对：过滤碎片后按行序对齐，整页非降才采信（页码向下递增）
                seq = [e.get("_rec_num") for e in ents]
                defined = [n for n in seq if n is not None]
                ok = len(defined) >= 2 and all(b >= a for a, b in zip(defined, defined[1:]))
                for e in ents:
                    rec = e.pop("_rec_num", None)
                    if e.get("printed") is None and rec is not None and (ok or len(defined) == 1):
                        e["printed"] = rec
                        e["_num_src"] = "right-strip"
                if not ok and len(defined) >= 2:
                    logger.info("p%d right-strip numbers not monotonic (%s); ignored", p, defined)
                # 第三层兜底：行内最后一个像页码的整数（OCR 常把行尾页码挤进
                # 点线/换行里）。整页单调门同样适用，违反则全部回退。
                for e in ents:
                    if e.get("printed") is None:
                        cands = [int(m) for m in re.findall(r"(?<!\d)(\d{1,3})(?!\d)", e["_raw_line"])
                                 if int(m) <= total]
                        if cands:
                            e["printed"] = cands[-1]
                            e["_num_src"] = "line-tail"

                def _mono():
                    vs = [e["printed"] for e in ents if e.get("printed") is not None]
                    return all(b >= a for a, b in zip(vs, vs[1:]))

                if not _mono():
                    reverted = 0
                    for e in ents:
                        if e.get("_num_src") == "line-tail":
                            e.pop("printed", None)
                            e.pop("_num_src", None)
                            reverted += 1
                    logger.info("p%d number recovery broke monotonicity; reverted %d", p, reverted)
                for e in ents:
                    e.pop("_raw_line", None)
                if ents:
                    for e in ents:
                        e["_src_page"] = p
                    layout_entries[p] = ents
            # 章行对齐：pp-doclayout 的内容框偶尔裁掉通栏章行的首字（第3章实测），
            # 墨缘右移会把章行错分到深层。书里章行必然同级——凡标题长得像
            # 「[第] N 章」的行，对齐到其中最浅的层级（仅修裁切伪影，不改写其余几何）
            chap_rows = [(r, e) for r, e in all_pairs if r.get("level") is not None and _is_chapter_title(e["title"])]
            if len(chap_rows) >= 2:
                shallow = min(r["level"] for r, _ in chap_rows)
                mis = [(r["level"], e["title"][:14]) for r, e in chap_rows if r["level"] != shallow]
                for r, _ in chap_rows:
                    r["level"] = shallow
                if mis:
                    logger.info("chapter rows realigned to L%d (was %s)", shallow, mis[:3])
            # 兄弟行层级一致：同父编号（6.5.1/6.5.2/6.5.3 共父「6.5」）的行必然同级。
            # 扫描歪斜会把个别行的墨缘推过聚类容差中线（6.5.1 实测滑到节的簇里）
            sib: dict[str, list[tuple[dict, dict]]] = {}
            for r, e in all_pairs:
                if r.get("level") is None:
                    continue
                m = re.match(r"^(\d{1,3}(?:\.\d{1,3})*)\.(\d{1,3})(?!\d)", e["title"])
                if m:
                    sib.setdefault(m.group(1), []).append((r, e))
            for parent, items in sib.items():
                if len(items) < 2:
                    continue
                levels = [r["level"] for r, _ in items]
                mode = Counter(levels).most_common(1)[0][0]
                for r, e in items:
                    if r["level"] != mode:
                        logger.info("sibling level fix %r L%d->L%d",
                                    e["title"][:22], r["level"], mode)
                        r["level"] = mode
            for r, ent in all_pairs:
                if r.get("level") is not None:
                    ent["level"] = r["level"]
                    ent["low_confidence"] = False
                    ent["_level_src"] = "geometry"
            logger.info("layout path parsed pages %s", {p: len(v) for p, v in layout_entries.items()})
        except Exception as e:  # noqa: BLE001 - endpoint down etc -> full fallback
            logger.warning("layout path failed entirely (%s); falling back to doc-parser", e)

    fail_pages = [] if llm_entries else [p for p in toc_phys if p not in layout_entries]
    transcripts = _transcribe_pages(doc, fail_pages, min(dpi, 150)) if fail_pages else []

    logger.info("[step 4/6] merging entries (llm -> layout -> transcript rules)")
    all_entries, all_like = [], 0
    page_methods: list[str] = []
    for p in toc_phys:
        if p in llm_entries:
            ents = llm_entries[p]
            like = len(ents)
            method = "llm"
        elif p in layout_entries:
            ents = layout_entries[p]
            like = len(ents)          # 结构化来源：解析率恒定满额，不拖低整体门槛
            method = "layout"
        elif p in fail_pages:
            idx = fail_pages.index(p)
            text, method, meta = transcripts[idx]
            trimmed = trim_repetition(text)
            ents, like = parse_transcript(trimmed)
            if not ents:
                # 该页可能整页被丢了右列页码：先按无编号收条目；
                # 条带配对推迟到全部页面解析完（需要下一张目录页的首个页码当上界）
                ents_nl, like_nl = parse_transcript(trimmed, allow_numberless=True)
                if len(ents_nl) >= 5:
                    ents, like = ents_nl, like_nl
                    method = method + "+numberless"
            for e in ents:
                e.setdefault("_src_page", p)
        else:
            continue  # LLM 接管窗口中被它拒绝的页：判定非目录页，直接跳过
        all_entries.extend(ents)
        all_like += like
        page_methods.append(method)
        logger.info("physical page %d method=%s lines->entries=%d entry_like=%d",
                    p, method, len(ents), like)
    rate = (len(all_entries) / all_like) if all_like else 0.0
    logger.info("parsed %d entries, parse_rate=%.2f", len(all_entries), rate)

    # 条带 OCR 配对补救：转录丢右列页码的页，用下一张目录页的首个页码做上界过滤后重试
    min_by_page = {}
    for e in all_entries:
        if e.get("printed") is not None and e.get("_src_page") is not None:
            p = e["_src_page"]
            min_by_page[p] = min(min_by_page.get(p, 10**9), e["printed"])

    def _bound_for(p):
        later = [q for q in min_by_page if q > p]
        return min(min_by_page[q] for q in later) if later else None

    recovered_pages = []
    for phys in toc_phys:
        page_ents = [e for e in all_entries if e.get("_src_page") == phys]
        if page_ents and sum(1 for e in page_ents if e.get("printed") is not None) < 0.5 * len(page_ents):
            before = sum(1 for e in page_ents if e.get("printed") is not None)
            _recover_missing_numbers(doc, phys, page_ents, max_num=_bound_for(phys))
            after = sum(1 for e in page_ents if e.get("printed") is not None)
            if after > before:
                recovered_pages.append({"page": phys, "recovered": after - before})
                logger.info("p%d: strip pairing recovered %d numbers", phys, after - before)
    numbered_entries = [e for e in all_entries if e.get("printed") is not None]
    numberless_entries = [e for e in all_entries if e.get("printed") is None]

    mode = "offset"
    anchor_info: dict | None = None
    sampled: dict | None = None
    segments: list | None = None
    probe_info: dict = {}
    attach_info: dict | None = None

    def _finish_anchor(entries_for_scan: list[dict], why: str) -> list[dict]:
        """v2 fallback: locate entries physically by ordered body scan."""
        nonlocal mode, anchor_info
        mode = "anchor"
        logger.info("[step 5b/6] ANCHOR-SCAN mode (%s): ordered body scan from p%d "
                    "for %d entries", why, max(toc_phys) + 1, len(entries_for_scan))
        stats = _anchor_scan(doc, entries_for_scan, max(toc_phys) + 1)
        _anchor_validate(entries_for_scan, stats)
        anchor_info = stats
        return [e for e in entries_for_scan if e.get("physical")]

    use_anchor = force_anchor or (
        not numbered_entries and len(numberless_entries) >= ANCHOR_MIN_ENTRIES)

    if use_anchor:
        why = "forced" if force_anchor else "TOC prints no usable page numbers"
        written = _finish_anchor(list(all_entries), why)
        if rate < PARSE_RATE_MIN:
            logger.info("parse_rate %.2f below %.2f but anchor scan recovered %d entries",
                        rate, PARSE_RATE_MIN, len(written))
    else:
        if not numbered_entries:
            raise InjectTocError(
                4, "no entries with printed page numbers survived parsing/recovery "
                   f"(and only {len(numberless_entries)} numberless entries - too few to anchor-scan)",
                {"parse_rate": round(rate, 3)})
        if rate < PARSE_RATE_MIN:
            dump = "\n".join(
                f"=== physical page {p} ({m}) ===\n{t}"
                for p, (t, m, _) in zip(fail_pages, transcripts)
            )
            raise InjectTocError(4, f"parse rate {rate:.2f} < {PARSE_RATE_MIN}",
                                 {"parse_rate": round(rate, 3), "transcripts": dump})

        logger.info("[step 5a/6] offset mapping + sample check (%d)", sample_check)
        segments = toc_mod.detect_page_segments(pdf_path)
        degenerate = _is_degenerate_segments(segments, total)
        probe_info = {}
        if degenerate:
            # 无文字层的书：分段回落 offset=0 是假设不是事实，必须用条目锚点实测
            earliest = sorted(numbered_entries, key=lambda e: e["printed"])[:3]
            logger.info("degenerate segments -> probing offset via %d earliest entries "
                        "(first %r printed %d), skipping TOC pages <= %d",
                        len(earliest), earliest[0]["title"][:30], earliest[0]["printed"],
                        max(toc_phys))
            off, probe_info = _probe_offset_by_anchor(doc, total, earliest, toc_phys, shapes)
            probe_info["targets"] = [{"title": e["title"], "printed": e["printed"]}
                                     for e in earliest]
            if off is None:
                logger.warning("offset probe failed (%s) -> falling back to anchor scan",
                               probe_info.get("note"))
                segments = None
            else:
                segments = [{"start": 1, "end": total, "offset": off}]
                probe_info["applied_offset"] = off
                logger.info("probed offset=%d", off)
        if segments is not None:
            for ent in numbered_entries:
                ent["physical"] = toc_mod.resolve_physical(ent["printed"], segments)
            # 分段来自探测（而非文字层证据）时，抽验只认独立证据：标题必须在目标页上真实出现
            sampled = _sample_check(numbered_entries, pdf_path, segments, sample_check,
                                    require_independent=degenerate)
            logger.info("sample: pass=%d fail=%d rate=%.2f",
                        sampled["pass"], sampled["fail"], sampled["pass_rate"])
            if sampled["details"] and sampled["pass_rate"] < SAMPLE_PASS_MIN:
                # offset 证据不足（探针被引用页骗过等）→ 不写错大纲，降级锚点扫描
                logger.warning("offset verification failed (%.2f) -> falling back to anchor scan",
                               sampled["pass_rate"])
                segments = None
        if segments is not None:
            # v3.1: 无印刷页码的条目（講義N 章扉等）不丢弃——邻居夹逼 + AV 强匹配钉回。
            # toc 页本身含同样的标题文字，必须排除，否则会钉到目录页上。
            toc_like = set(llm_entries) if llm_entries else set(toc_phys)
            attached, attach_fail = _attach_unnumbered(
                doc, list(all_entries), toc_like, shapes)
            written = sorted(numbered_entries + attached,
                             key=lambda e: (e.get("physical") or 0, -e["level"]))
            attach_info = {"attached": len(attached),
                           "failed": attach_fail}
        else:
            attach_info = None
            written = _finish_anchor(list(all_entries), "offset mapping absent or unverified")

    doc.close()

    logger.info("[step 6/6] backup + write outline")
    # 结构守恒门（v3.1）：LLM 读进的条目数是「读到的目录」的规模下限；
    # 写出量远低于它 = 有条目被静默吞掉（v3.1 前 講義N 章扉就是这么丢的，
    # 31 节塌缩成假单章）。宁可拒绝也不写结构性残缺的大纲。
    accepted_n = sum(len(v) for v in llm_entries.values()) if llm_entries else len(all_entries)
    keep_ratio = (len(written) / accepted_n) if accepted_n else 1.0
    min_written = max(4, total // MIN_WRITTEN_DIVISOR)
    hard_floor = max(min_written, int(accepted_n * STRUCT_HARD_MIN))
    if len(written) < hard_floor:
        raise InjectTocError(
            4, f"outline lost entries: wrote {len(written)} but {accepted_n} were "
               f"parsed (keep ratio {keep_ratio:.2f} < {STRUCT_HARD_MIN:.2f}); "
               f"refusing to write a structurally mutilated outline.",
            {"written_preview": [e["title"] for e in written[:10]],
             "dropped_preview": sorted(
                 {e["title"] for e in all_entries if e.get("physical") is None})[:10],
             "toc_physical_pages": toc_phys,
             "tried_windows": tried_windows,
             "attach": attach_info,
             "llm_parse": llm_meta if not no_llm else {"skipped": True}})
    outline = [[e["level"], e["title"], e["physical"]] for e in written]
    outline = _sanitize_outline(outline)
    sidecar = toc_mod.backup_toc(pdf_path)
    toc_mod.set_toc(pdf_path, outline)

    levels = Counter(e["level"] for e in written)
    methods = Counter(page_methods)
    report = {
        "pdf": pdf_path,
        "mode": mode,
        "written_entries": len(outline),
        "levels": {str(k): levels[k] for k in sorted(levels)},
        "low_confidence": sum(1 for e in written if e.get("low_confidence")),
        "parse_rate": round(rate, 3),
        "toc_physical_pages": toc_phys,
        "window_score": window_score,
        "transcription_methods": dict(methods),
        "llm_parse": ({"skipped": True} if no_llm else
                      {"pages_parsed": sorted(llm_entries),
                       "entries": llm_meta.get("entries", 0),
                       "tried_windows": tried_windows}),
        "unnumbered_attach": attach_info,
        "keep_ratio": round(keep_ratio, 3),
        "strip_recovered": recovered_pages,
        "sampled": sampled,
        "segments": segments,
        "offset_probe": probe_info if mode == "offset" else {},
        "anchor_scan": anchor_info,
        "backup_path": sidecar,
        "elapsed_s": round(time.time() - t0, 1),
    }
    logger.info("done: %d entries written in %.1fs (mode=%s)",
                len(outline), report["elapsed_s"], mode)
    return report


# ============================================================================
# check-toc: validate an EXISTING embedded outline against page content
# ============================================================================

CHECK_MIN_AGREE = 3      # distinct samples that must vote for the same shift
CHECK_OCR_CALL_LIMIT = max(0, int(os.environ.get("PDFX_CHECK_OCR_CALLS", "60")))
CHECK_PASS_MIN = 0.5     # below this with no consensus shift -> suspect


def _entry_evidence(doc, ent: dict, dpi: int) -> tuple[str, str]:
    """Return (page_text, tier) found at ent's candidate physical page.

     Tier "text" = PDF text layer. Tier "native" = native OCR of the rendered
     page. Empty string = no usable evidence.
    """
    phys = ent["physical"]
    if not (1 <= phys <= len(doc)):
        return "", "oob"
    page = doc[phys - 1]
    text = (page.get_text() or "").strip()
    if text:
        return text, "text"
    from ._vlhttp import render_page_png

    try:
        png = render_page_png(page, dpi=150)
        return ocr_apple.ocr_png(png) or "", "native"
    except Exception as e:  # noqa: BLE001
        logger.warning("check-toc: ocr failed on physical p%d: %s", phys, e)
        return "", "native-failed"


def _title_at(title: str, page_text: str, tier: str) -> str | None:
    """Match kind when `title` is evidenced on `page_text`.

     Native OCR evidence only counts for the strong tiers, so weak/fuzzy hits
     are not trusted there.
    """
    kind = _match_title(title, page_text)
    if kind is None:
        return None
    if tier == "text":
        return kind
    return kind if kind in STRONG_KINDS else None


def _shift_candidates(segments: list | None) -> list[int]:
    """Ordered candidate shifts: segment-derived offsets first (the classic
    failure is printed-page-numbers-written-as-physical), then ascending 1..N,
    then a small negative tail. Zero excluded (that is phase 1)."""
    cands: list[int] = []
    for seg in segments or []:
        off = seg.get("offset")
        if isinstance(off, int) and off != 0 and off not in cands:
            cands.append(off)
    cands += [s for s in range(1, PROBE_MAX_OFFSET + 1) if s not in cands]
    cands += [s for s in range(-10, 0) if s not in cands]
    return cands


def run_check(pdf_path: str, samples: int = 5, dpi: int = 200) -> dict:
    """Read-only health check of an existing embedded outline.

    Verdicts:
      pass         - every sampled title evidenced at its written physical
                     page (anomalies ride along in anomaly_notes)
      offset_shift - >=CHECK_MIN_AGREE failed samples independently hit at
                     written_page + suggested_shift; repair via apply_outline_shift
      suspect      - failures with no consistent shift; do NOT auto-repair
      empty        - no outline present (caller should inject-toc instead)

    Never writes to the PDF.
    """
    import pymupdf as fitz

    t0 = time.time()
    pdf_path = os.path.abspath(pdf_path)
    if not os.path.exists(pdf_path):
        raise InjectTocError(1, f"file not found: {pdf_path}")

    doc = fitz.open(pdf_path)
    total = len(doc)
    outline = doc.get_toc()
    stats = {
        "entries": len(outline),
        "levels": {str(k): v for k, v in sorted(Counter(lv for lv, _, _ in outline).items())},
        "total_pages": total,
    }
    if not outline:
        doc.close()
        return {"pdf": pdf_path, "verdict": "empty", **stats,
                "note": "no embedded outline - run inject-toc"}

    # deterministic even spread across the outline (idempotent re-runs)
    n = min(samples, len(outline))
    idxs = sorted({round(i * (len(outline) - 1) / max(1, n - 1)) if n > 1 else 0
                   for i in range(n)})
    ents = [{"title": t.strip(), "physical": p}
            for _, t, p in [outline[i] for i in idxs]]

    logger.info("check-toc: %d/%d entries sampled at written pages", len(ents), len(outline))
    details, failed = [], []
    for ent in ents:
        text, tier = _entry_evidence(doc, ent, dpi)
        ok_kind = _title_at(ent["title"], text, tier)
        ent["shift_votes"] = set()
        details.append({"title": ent["title"], "written_page": ent["physical"],
                        "evidence_tier": tier, "passed": ok_kind is not None,
                        "match_kind": ok_kind})
        if ok_kind is None:
            failed.append(ent)

    verdict = None
    suggested_shift = None
    av_calls = 0
    votes: dict[int, set[str]] = {}
    if not failed:
        verdict = "pass"
    else:
        segments = toc_mod.detect_page_segments(pdf_path)
        cands = _shift_candidates(segments)
        logger.info("phase-1 failures: %d/%d; searching %d candidate shifts",
                    len(failed), len(ents), len(cands))
        votes: dict[int, set[str]] = {}
        for s in cands:
            for ent in failed:
                cand = dict(ent)
                cand["physical"] = ent["physical"] + s
                if not (1 <= cand["physical"] <= total):
                    continue
                need_ocr = False
                page = doc[cand["physical"] - 1]
                text = (page.get_text() or "").strip()
                tier = "text"
                if not text:
                    if av_calls >= CHECK_OCR_CALL_LIMIT:
                        continue
                    need_ocr = True
                if need_ocr:
                    av_calls += 1
                    text, tier = _entry_evidence(doc, cand, dpi)
                kind = _title_at(ent["title"], text, tier) if text else None
                if kind:
                    votes.setdefault(s, set()).add(ent["title"])
            best_s = max(votes, key=lambda k: len(votes[k])) if votes else None
            if best_s is not None and len(votes[best_s]) >= CHECK_MIN_AGREE:
                suggested_shift = best_s
                break
        if suggested_shift is not None:
            verdict = "offset_shift"
        elif len(failed) / len(details) > 1 - CHECK_PASS_MIN:
            verdict = "suspect"
        else:
            verdict = "pass"

    anomalies = [d["title"] for d in details if not d["passed"]]
    # v3.1 结构异常备注：大书却只有个位数顶层节点 = 层级大概率塌了
    #（点级抽验看不见这种伤，必须单独点名）
    l1_n = stats["levels"].get("1", 0)
    if len(outline) >= 20 and l1_n <= 1:
        anomalies.append(f"structural anomaly: {len(outline)}-entry outline has "
                         f"{l1_n} top-level node(s) - hierarchy likely collapsed")
    doc.close()
    report = {
        "pdf": pdf_path,
        "verdict": verdict,
        "suggested_shift": suggested_shift,
        "sampled": len(details),
        "failed": len(anomalies),
        "anomaly_notes": [
            f"title not evidenced at written page: {t}" for t in anomalies],
        "offset_votes": {str(s): sorted(v) for s, v in votes.items()},
        **stats,
        "elapsed_s": round(time.time() - t0, 1),
    }
    if verdict == "offset_shift":
        report["repair"] = (f"run: pdfx cli.py check-toc <pdf> --apply-shift "
                            f"{suggested_shift}")
    logger.info("check-toc verdict=%s (%.1fs)", verdict, report["elapsed_s"])
    return report


def apply_outline_shift(pdf_path: str, shift: int) -> dict:
    """Rewrite the existing outline with every page shifted by `shift`
    (positive = content actually sits further into the book).

    Refuses when any shifted page would leave the book. Backs up first
    (backup_toc keeps the earliest sidecar, so the original publisher
    outline survives any number of repairs).
    """
    import pymupdf as fitz

    if shift == 0:
        raise InjectTocError(9, "shift must be non-zero")
    pdf_path = os.path.abspath(pdf_path)
    if not os.path.exists(pdf_path):
        raise InjectTocError(1, f"file not found: {pdf_path}")
    doc = fitz.open(pdf_path)
    total = len(doc)
    outline = doc.get_toc()
    doc.close()
    if not outline:
        raise InjectTocError(9, "PDF has no outline to shift")

    moved = [[lv, t, p + shift] for lv, t, p in outline]
    bad = [(t, p) for lv, t, p in moved if not (1 <= p <= total)]
    if bad:
        raise InjectTocError(
            9, f"shift {shift} moves {len(bad)} entr(y/ies) out of range "
               f"(first: {bad[0]!r}); refuse to write partial outline",
            {"out_of_range": bad[:5]})
    sidecar = toc_mod.backup_toc(pdf_path)
    toc_mod.set_toc(pdf_path, _sanitize_outline(moved))
    logger.info("apply-shift %+d: %d entries rewritten (backup: %s)",
                shift, len(moved), sidecar)
    return {"pdf": pdf_path, "applied_shift": shift,
            "entries_rewritten": len(moved), "backup_path": sidecar}
