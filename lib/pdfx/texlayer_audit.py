"""Text-layer semantic credibility audit.

Why this exists: quality.py's char-legality tiers cannot see "legal garbage"
- publisher embedded OCR layers whose characters are all legal but semantically
  scrambled.
This module dual-reads every char-trusted page (embedded text layer vs an
independent native OCR read of the rendered page) and scores CJK/kana
k-gram agreement.

Formula regions are excluded BY DESIGN: both engines misread math, each in
its own way, so formula disagreement carries no signal about layer quality.
Math repair is delegated to the configured visual extraction chain.

Per-page verdict (only pages whose char tier is "trusted" get judged):
  trusted - layer bigrams well attested in the independent read
  suspect - clear disagreement, or ambiguous band where the layout-aware
            arbiter vote fails to corroborate the layer (FN-averse default)
  n/a     - too little CJK prose to compare (covers, number tables)

Pipeline position: after text extraction, before downstream import.
Repair stays with the caller's visual re-OCR workflow; this module only locates
damage and estimates the work.

Artifacts (local source of truth; metadata warning lines are projections):
  <extraction_dir>/_texlayer_audit.json    page detail + config + resume state
  <extraction_dir>/00_book_metadata.md     idempotent audit section
  <extraction_dir>/**/text.md              frontmatter 文字层审计 field

Design note: ensemble agreement is used as a routing signal; correction
magnitude is used as a post-repair acceptance signal. Bibliographic examples
and private corpus references are intentionally kept out of this module.

Runtime note (kept hardware-neutral):
  - OCR engine placement and power telemetry are environment-dependent.
  - Choose concurrency from fresh synthetic benchmarks; do not infer it from
    one machine's power or throughput measurements.
  - Benchmarks MUST use never-seen pages so model-server caches do not distort
    measurements.

v3 sample-first fast path: a zero-model-cost digital-native gate
(scan_signature) checks whether EVERY char-trusted page looks born-digital -
structural tells only, no OCR: full-page image coverage (>0.9) and
majority-invisible text (Tr 3, the classic publisher-OCR-overlay rendering).
Ground truth anchors should be synthetic or maintained outside this source tree.
Gate PASS -> dual-read
only a random 20-page prefix of the shuffled order; clean sample finalizes the
document as mode="sampled" (unread sections inherit trusted, provenance recorded);
ANY suspect - or an inconclusive sample - escalates to the legacy full sweep.
Gate FAIL (trusted pages carry scan/overlay signatures = fake-digital
publisher OCR layers) goes straight to the full sweep. Metadata/fonts are
recorded as evidence only - metadata lies, structure doesn't.

v2 arbitration: ambiguous pages first try a
prose-crop path - paragraphs clustered from the LAYER's own span geometry
(cmap corruption moves characters, never boxes), cropped, sent straight to
the configured region OCR provider ("OCR:" prompt), bypassing the layout
re-segmentation and skipping formula/table VL work the CJK metric discards
anyway. Concatenated k=1 containment vs layer text clipped to the same
rects. Any block failure escalates once to the legacy whole-page layout-aware
path, then suspect. Global block concurrency is configurable. State flushes
every FLUSH_EVERY pages so a crash loses at most the
  last batch may be lost if a finalization error occurs before state is persisted.
 """

from __future__ import annotations

import json
import logging
import os
import random
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import ocr_apple
from . import ocr_docparser
from ._vlhttp import OCRError, provider_model, render_page_png
from .quality import scan_pdf, cjk_skeleton

logger = logging.getLogger(__name__)

TRUSTED_MIN = 0.85        # unigram containment >= this -> trusted
SUSPECT_MAX = 0.60        # unigram containment <= this -> clear disagreement
ARBITRATE_LO = SUSPECT_MAX
ARBITRATE_HI = TRUSTED_MIN
ARB_TRUST_MIN = 0.60      # arbiter containment that corroborates the layer
MAX_ARBITERS = max(0, int(os.environ.get("PDFX_MAX_ARBITERS", "250")))
ARBITER_WORKERS = max(1, int(os.environ.get("PDFX_ARBITER_WORKERS", "3")))
PAGE_VL_WORKERS = max(1, int(os.environ.get("PDFX_PAGE_VL_WORKERS", "4")))
MIN_CJK_CHARS = 24        # less CJK prose than this -> n/a (covers, 数表)
K = 2                     # k-gram size for the (secondary) order-sensitive signal

# --- v2 arbitration constants: prose crops + direct VL ---
LINE_GAP_FACTOR = 1.5     # 相邻行纵向间距 <= 此倍行高 -> 归同一段
PROSE_CJK_RATIO = 0.5     # 行内 CJK(汉字+假名) 占比 >= 此值才算散文行
PROSE_MIN_SKELETON = 4    # 散文行至少的 CJK/假名字数（滤掉零星符号行）
CROP_TARGET_PX = 1200     # crop long-edge target pixels
CROP_MAX_ZOOM = 12.0
PAD_TOP_PT = 4.0          # 上垫防切振假名（ruby 约占 0.3-0.5em）
PAD_SIDE_PT = 2.0
PAD_BOTTOM_PT = 2.0
VL_MODEL_DIRECT = provider_model("region", "region-ocr")
VL_OCR_PROMPT = "OCR:"
VL_MAX_TOKENS = max(1, int(os.environ.get("PDFX_REGION_MAX_TOKENS", "2048")))
BLOCK_SEM_SIZE = max(1, int(os.environ.get("PDFX_BLOCK_CONCURRENCY", "2")))
BLANK_INK_RATIO = 0.0002  # 裁剪/整页墨量占比低于此 -> 视为空白，不调 VL
FLUSH_EVERY = 16          # 边跑边存：主扫每定稿这么多页刷一次状态盘
ARB_TRUST_PROSE_MIN = 0.75  # v2 判定：主段落字符加权重合率 >= 此值 -> trusted
                           # Tune against a synthetic labeled set; formula regions
                           # remain outside this semantic audit by design.
BLOCK_FLOOR_CHARS = 20    # 实质大段门槛：主段落块字数达到此值才适用底线规则
BLOCK_FLOOR_SCORE = 0.70  # 实质大段重合率低于此 -> 无论加权均值直接 suspect。
                           # Tune with representative prose blocks; keep the
                           # threshold conservative to avoid false acceptance.

SECTION_SUSPECT_RATIO = 0.30          # section auto-repair trigger
REPAIR_EST_SEC_PER_PAGE = max(0, int(os.environ.get("PDFX_REPAIR_SEC_PER_PAGE", "30")))
REPAIR_SOFT_GATE_SECONDS = max(
    0, int(os.environ.get("PDFX_REPAIR_SOFT_GATE_SECONDS", str(45 * 60)))
)

# --- v3 digital-native sample fast path ---
SAMPLE_SIZE_DEFAULT = 20              # 抽检页数；取自洗牌序前缀 = 均匀随机样本
SAMPLE_MIN_TRUSTED = SAMPLE_SIZE_DEFAULT // 2
                                      # 样本 trusted 少于此值且无 suspect：CJK 太薄、
                                      # 对读本来就无法判（如实记 note，不升级全量——
                                      # 全量对这些页同样只会产出 n/a）
SCAN_COVER_RATIO = 0.9                # 与 quality.classify_pages 同阈值：整页大图=扫描底图
OVERLAY_INVISIBLE_RATIO = 0.5         # 隐形文字(Tr 3)占比过半 -> OCR 覆盖层渲染特征
OVERLAY_MIN_CHARS = 30                # 页面字数低于此不做隐形判定（防封面/空白误报）
FONT_SAMPLE_PAGES = 12                # 字体清单抽样页数（仅证据记录，不参与门控）

AUDIT_JSON_NAME = "_texlayer_audit.json"
META_BEGIN = "<!-- TEXLAYER_AUDIT:BEGIN -->"
META_END = "<!-- TEXLAYER_AUDIT:END -->"


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------

_DOT_LEADER_RE = re.compile("\u30fb")   # ・ 引导点（索引页点线），假名区会污染块统计


def arb_skeleton(text: str) -> str:
    """Arbitration skeleton with U+30FB removed from the CJK skeleton.

    Dot leaders can otherwise inflate the character count while differing
    between page text and OCR output. The primary metric remains unchanged.
    """
    return _DOT_LEADER_RE.sub("", cjk_skeleton(text))


def _ngrams(seq: str, k: int) -> set[str]:
    return {seq[i:i + k] for i in range(len(seq) - k + 1)} if len(seq) >= k else set()


def containment(layer_text: str, reference_text: str, k: int = 1) -> float:
    """Share of the LAYER's CJK n-grams attested in the REFERENCE read.

    Directional on purpose: the question is 'does an independent engine
    corroborate what the embedded layer claims', not symmetric similarity.
    Default k=1 (character-set level): order-insensitive, immune to ruby
    (furigana) interleaving and column-order differences between engines -
    measured on a dense TOC layout where bigram scoring pushed a large share of
    pages into the ambiguous band purely from ruby ordering noise. Garbled
    layers still fail hard here: their invented characters are absent from
    the independent read. Pass k=2 for the order-sensitive secondary signal.
    """
    lb = _ngrams(cjk_skeleton(layer_text), k)
    rb = _ngrams(cjk_skeleton(reference_text), k)
    if not lb:
        return 1.0
    return round(len(lb & rb) / len(lb), 4)


def classify_agreement(score: float) -> str:
    if score >= TRUSTED_MIN:
        return "trusted"
    if score <= SUSPECT_MAX:
        return "suspect"
    return "ambiguous"


# ---------------------------------------------------------------------------
# frontmatter helpers
# ---------------------------------------------------------------------------

_FM_KEY = "文字层审计"


def _set_frontmatter_key(md_path: Path, updates: dict[str, str]) -> None:
    text = md_path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return
    end = text.find("\n---", 3)
    if end == -1:
        return
    block, rest = text[:end], text[end:]
    for key, value in updates.items():
        pat = re.compile(rf"^{re.escape(key)}:.*$", re.M)
        line = f"{key}: {value}"
        if pat.search(block):
            block = pat.sub(line, block, count=1)
        else:
            block += f"\n{line}"
    md_path.write_text(block + rest, encoding="utf-8")


def _parse_page_range(raw: str) -> list[int]:
    """'44 〜 53' / 'p.8-15' / '12' -> [44..53] etc (physical pages)."""
    nums = [int(n) for n in re.findall(r"\d+", raw or "")]
    if not nums:
        return []
    if len(nums) >= 2 and nums[1] >= nums[0]:
        lo, hi = nums[0], min(nums[1], nums[0] + 400)
        return list(range(lo, hi + 1))
    return [nums[0]]


def map_sections(extraction_dir: Path) -> list[dict]:
    """extraction/**/text.md -> [{title, path, pages}] from frontmatter."""
    out = []
    seen: set[Path] = set()
    for md in sorted(p for p in extraction_dir.rglob("*.md")
                     if not p.name.startswith(("00_", "_"))):
        if md.resolve() in seen:
            continue
        seen.add(md.resolve())
        text = md.read_text(encoding="utf-8")
        if not text.startswith("---"):
            continue
        end = text.find("\n---", 3)
        fm = text[3:end] if end != -1 else ""
        title_m = re.search(r'^title:\s*"(.*)"\s*$', fm, re.M)
        phys_m = re.search(r"^PDF物理页码:\s*\"(.*)\"\s*$", fm, re.M)
        if not (title_m and phys_m):
            continue
        out.append({"title": title_m.group(1), "path": md,
                    "pages": _parse_page_range(phys_m.group(1))})
    return out


# ---------------------------------------------------------------------------
# prose region detection (v2 arbitration)
# ---------------------------------------------------------------------------

def _render_clip(page, clip, zoom: float) -> tuple[bytes, float]:
    """渲染页内一个矩形区域（散文裁剪用）。返回 (png_bytes, ink_ratio)。
    ink_ratio = 墨量占比：任一通道明显暗（<200）的像素比例，空白预检用。"""
    import pymupdf as fitz

    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
    png = pix.tobytes("png")
    ink = 0.0
    try:
        import numpy as np
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, pix.n)
        ink = float((arr.min(axis=-1) < 200).mean())
    except Exception:  # noqa: BLE001 - 统计失败不阻断仲裁
        ink = 1.0       # 保守：算不出就当有墨，照常调 VL
    return png, ink


def _prose_lines(page) -> list:
    """dict 提取散文行：[(Rect, skeleton_len)]。

    CJK 占比与最小字数双门槛：公式行/数表行符号数字为主，占比天然低于门槛。
    ruby（振假名）行全是假名，照样通过——它会被聚段并进所属段落矩形，
    两边（layer clip 与裁剪图）看到的内容保持一致。
    """
    import pymupdf as fitz

    out = []
    for rect, txt in _all_lines(page):
        skel_len = len(cjk_skeleton(txt))
        if skel_len >= PROSE_MIN_SKELETON and \
                skel_len / max(1, len(txt.strip())) >= PROSE_CJK_RATIO:
            out.append((fitz.Rect(rect), skel_len))
    return out


def _all_lines(page) -> list:
    """全部文本行 [(Rect, raw_text)]，含公式行/数表行（供二次扫兜底）。"""
    import pymupdf as fitz

    out = []
    for blk in page.get_text("dict").get("blocks", []):
        if blk.get("type") != 0:
            continue
        for ln in blk.get("lines", []):
            txt = "".join(s.get("text", "") for s in ln.get("spans", []))
            if txt.strip():
                out.append((fitz.Rect(ln["bbox"]), txt))
    return out


def _cluster_paragraphs(lines: list, page_rect=None) -> list:
    """行 -> 段矩形列表 [{rect, chars}]。

    按 y 排序后逐行找可归并的最近段：横向重叠不足窄边 30% 不并（双栏/
    旁注靠这条分开），纵向间距超过 LINE_GAP_FACTOR 倍行高不并（段间距/
    标题与正文的分界）。负 gap（bbox 纵向本就交叠，如 ruby 行）视为紧邻。
    """
    items = sorted(lines, key=lambda t: (t[0].y0, t[0].x0))
    clusters: list[dict] = []
    for rect, n_chars in items:
        best, best_gap = None, None
        for c in clusters:
            lr = c["lines"][-1]
            x_ov = min(rect.x1, lr.x1) - max(rect.x0, lr.x0)
            if x_ov < min(lr.width, rect.width) * 0.3:
                continue
            gap = rect.y0 - lr.y1
            if gap > LINE_GAP_FACTOR * max(lr.height, rect.height):
                continue
            if best is None or abs(gap) < abs(best_gap):
                best, best_gap = c, gap
        if best is None:
            clusters.append({"lines": [rect], "chars": n_chars})
        else:
            best["lines"].append(rect)
            best["chars"] += n_chars
    out = []
    for c in clusters:
        u = c["lines"][0]
        for r in c["lines"][1:]:
            u |= r
        u = fitz_pad(u)
        if page_rect is not None:
            u &= page_rect
        if not u.is_empty:
            out.append({"rect": u, "chars": c["chars"]})
    return out


def fitz_pad(r):
    """段落矩形外扩垫边：上垫防切振假名，四周小垫防贴边裁字。"""
    import pymupdf as fitz

    return fitz.Rect(r.x0 - PAD_SIDE_PT, r.y0 - PAD_TOP_PT,
                     r.x1 + PAD_SIDE_PT, r.y1 + PAD_BOTTOM_PT)


def _png_ink_ratio(png_bytes: bytes) -> float:
    """PNG 墨量占比；解析失败返回 1.0（保守按有墨处理）。"""
    try:
        import numpy as np
        from PIL import Image
        import io
        arr = np.asarray(Image.open(io.BytesIO(png_bytes)).convert("RGB"))
        return float((arr.min(axis=-1) < 200).mean())
    except Exception:  # noqa: BLE001
        return 1.0


def make_arbiter(arb_doc, dpi: int):
    """v2 仲裁器工厂。返回 arbitrate(pno) -> entry-dict。

    路径：layer 行几何聚段 -> 散文矩形裁剪 -> 直连 PaddleOCR-VL 拼接比对。
    二次扫兜底：没被任何散文段落盖住、但仍携带 >= PROSE_MIN_SKELETON 个 CJK
    字的行（公式行里藏的坏字、含数学记号的标题）聚成补充矩形一并裁剪——
    只记录进 JSON 供复盘，不参与判定（公式区按章程在审计范围外）。
    判定 = 主段落块字符加权平均重合率 vs ARB_TRUST_PROSE_MIN（标定见常量注）。
    升级链：无主段落 / 总 CJK < MIN_CJK_CHARS / 任一块 OCRError /
    本路径异常 -> 整页 Doc-Parser 旧路一次（阈值仍 ARB_TRUST_MIN）-> 再败 suspect。
    arb_lock 串行化全部 arb_doc 访问（PyMuPDF 非线程安全）；block_sem 把
    全局在飞 VL 调用压到 -np 2（同 doc_parser_app 元素池语义）。
    """
    from ._vlhttp import post_image

    arb_lock = threading.Lock()
    block_sem = threading.Semaphore(BLOCK_SEM_SIZE)

    def _whole_page_verdict(pno: int) -> dict:
        """Fallback path: send the whole page to the layout-aware adapter.
        空白页短路：墨量≈0 且文字层也空 -> 直接 trusted，不调 VL
        （空白页的 VL 只会撞幻觉，且整页 Doc-Parser 更贵）。"""
        with arb_lock:
            png = render_page_png(arb_doc[pno - 1], dpi=dpi)
            layer_full = arb_doc[pno - 1].get_text() or ""
        if not layer_full.strip() and _png_ink_ratio(png) < BLANK_INK_RATIO:
            return {"verdict": "trusted", "arbiter_score": 1.0,
                    "mode": "blank_page"}
        ref = ocr_docparser.ocr_png(png)
        score = containment(layer_full, ref, k=1)
        return {"verdict": "trusted" if score >= ARB_TRUST_MIN else "suspect",
                "arbiter_score": score, "mode": "whole_page"}

    def _prose_crop_verdict(pno: int) -> dict | None:
        """新路。返回 None = 本页不适合/失败于裁剪路径，调用方升级旧路。

        判定统计量 = 主段落块（占比门槛筛出的散文段）的字符加权平均重合率
        （ARB_TRUST_PROSE_MIN）。二次扫的补充块（公式行/索引读音等碎片）
        只记录不判定——公式区按章程在审计范围外（见模块 docstring 盲区）。"""
        with arb_lock:
            page = arb_doc[pno - 1]
            prim = _cluster_paragraphs(_prose_lines(page), page_rect=page.rect)
            kept = [pa["rect"] for pa in prim]
            loose = []
            for rect, txt in _all_lines(page):
                if len(cjk_skeleton(txt)) < PROSE_MIN_SKELETON:
                    continue
                if any(rect.intersects(r) for r in kept):
                    continue
                loose.append((rect, len(cjk_skeleton(txt))))
            groups = _cluster_paragraphs(loose, page_rect=page.rect)
            prects, layer_parts, roles = [], [], []
            for pa in prim:
                prects.append(pa["rect"])
                layer_parts.append(page.get_text(clip=pa["rect"]) or "")
                roles.append("primary")
            for pa in groups:
                prects.append(pa["rect"])
                layer_parts.append(page.get_text(clip=pa["rect"]) or "")
                roles.append("secondary")
        if not prects or \
                sum(len(arb_skeleton(t)) for t in layer_parts) < MIN_CJK_CHARS:
            return None

        # 并行化：先串行渲染全部非短路块（PyMuPDF 非线程安全，arb_lock），
        # 再把 VL 调用并发提交，全局仍受 block_sem(-np 2) 限流。
        # 页内并发让 3 个并行页的块都能排进 2 个槽，撮合零点不浪费。
        vl_jobs = []          # (rect, role, layer_part, png)
        blocks = []
        for i, r in enumerate(prects):
            long_pt = max(r.width, r.height, 1e-6)
            zoom = min(max(1.0, CROP_TARGET_PX / long_pt), CROP_MAX_ZOOM)
            with arb_lock:
                png, ink = _render_clip(arb_doc[pno - 1], r, zoom)
            is_secondary = roles[i] == "secondary"
            layer_part = _DOT_LEADER_RE.sub("", layer_parts[i])
            # 空白裁剪短路（墨量≈0）：不调 VL。layer 空 -> 空白对空白=1.0
            # （trusted，不误报）；layer 非空而渲染空白 -> 字消失=0.0 (suspect)。
            # secondary 块本就不参与判定（见注释），不再发 VL 省 ~10% 调用。
            if is_secondary or ink < BLANK_INK_RATIO:
                if is_secondary:
                    blocks.append({"bbox": [round(v, 1) for v in r],
                                   "role": "secondary",
                                   "chars": len(arb_skeleton(layer_parts[i])),
                                   "score": None, "skipped": True})
                else:
                    blocks.append({"bbox": [round(v, 1) for v in r],
                                   "role": "primary",
                                   "chars": len(arb_skeleton(layer_parts[i])),
                                   "score": 1.0 if not layer_part.strip() else 0.0,
                                   "blank_crop": True})
                continue
            vl_jobs.append((r, roles[i], layer_part, png))

        def _vl_one(idx: int, rect, role, layer_part, png):
            with block_sem:
                ref_i = post_image(VL_MODEL_DIRECT, png, VL_OCR_PROMPT,
                                    max_tokens=VL_MAX_TOKENS, role="region")
            return idx, rect, role, layer_part, ref_i

        if vl_jobs:
            with ThreadPoolExecutor(max_workers=min(PAGE_VL_WORKERS, len(vl_jobs))) as pool:
                futs = [pool.submit(_vl_one, i, *job) for i, job in enumerate(vl_jobs)]
                try:
                    for fut in futs:
                        idx, _r, _role, _lp, ref_i = fut.result()
                        blocks.append({"bbox": [round(v, 1) for v in _r],
                                       "role": _role,
                                       "chars": len(arb_skeleton(_lp)),
                                       "score": containment(
                                           _lp, _DOT_LEADER_RE.sub("", ref_i), k=1)})
                except OCRError as e:
                    logger.warning("prose block read failed p%s: %s", pno, e)
                    return None                 # 升级旧路，不引入假 suspect
            blocks.sort(key=lambda b: b["bbox"][1])   # 恢复纵向阅读序（结果回填）
        w_sum = w_tot = 0.0
        for b in blocks:
            if b["role"] == "primary" and b["chars"] > 0:
                w_sum += b["chars"] * b["score"]
                w_tot += b["chars"]
        if not w_tot:
            return None                          # 无主段落可判 -> 升级旧路
        primary_weighted = round(w_sum / w_tot, 4)
        sec_zero = sum(b["chars"] for b in blocks
                       if b["role"] == "secondary" and b.get("score") is not None
                       and b["score"] < 0.3)
        # 实质大段底线：坏字嵌在大段里时，加权均值会被干净小块稀释回线上
        # Borderline pages remain suspect when a substantial block disagrees.
        floor_hit = [b for b in blocks if b["role"] == "primary"
                     and b["chars"] >= BLOCK_FLOOR_CHARS
                     and b["score"] < BLOCK_FLOOR_SCORE]
        suspect = primary_weighted < ARB_TRUST_PROSE_MIN or bool(floor_hit)
        entry = {"verdict": "suspect" if suspect else "trusted",
                 "arbiter_score": primary_weighted, "mode": "prose_crop",
                 "blocks": blocks, "secondary_zero_chars": sec_zero}
        if floor_hit:
            entry["reason"] = "block-floor: " + ",".join(
                f"{b['score']:.2f}(c{b['chars']})" for b in floor_hit)
        return entry

    def arbitrate(pno: int) -> dict:
        try:
            res = _prose_crop_verdict(pno)
            if res is not None:
                return res
        except Exception as e:  # noqa: BLE001 - 裁剪路径任何意外都走升级，不外泄
            logger.warning("prose-crop arbitration crashed p%s: %s -> escalate", pno, e)
        try:
            return _whole_page_verdict(pno)
        except OCRError as e:
            logger.warning("arbiter failed p%s: %s", pno, e)
            return {"verdict": "suspect", "reason": "arbiter-unavailable"}

    return arbitrate


# ---------------------------------------------------------------------------
# v3 digital-native gate
# ---------------------------------------------------------------------------

def scan_signature(pdf_path: str, char_tiers: dict | None = None) -> dict:
    """Zero-model-cost page-origin fingerprint for the sample-first gate.

    Answers ONE question with PyMuPDF structure alone (no OCR, no model): does
    every page whose char tier is "trusted" look born-digital? Two independent
    structural tells separate a publisher OCR layer from real digital text:
      - full-page image coverage > SCAN_COVER_RATIO  (scan picture underneath);
      - majority-invisible text (render mode Tr 3)   (classic overlay layout).
    gate_pass = NO trusted-tier page carries either signature. Such a book may
    finalize from a small random sample (audit_pdf sampling="auto"); anything
    else falls back to the legacy full sweep - conservative by design.
    Producer metadata and the font inventory are recorded as evidence only.
    """
    import pymupdf as fitz

    doc = fitz.open(pdf_path)
    if char_tiers is None:
        char_tiers = {q.page: q.tier for q in scan_pdf(pdf_path)}
    meta = doc.metadata or {}
    total = len(doc)
    scan_like: list[int] = []
    overlay_like: list[int] = []
    trusted_scan_like: list[int] = []
    for i, page in enumerate(doc):
        pno = i + 1
        area = page.rect.width * page.rect.height or 1.0
        cov = 0.0
        for img in page.get_images(full=True):
            xref = img[0]
            for r in page.get_image_rects(xref):
                cov += r.width * r.height / area
        scan_flag = cov > SCAN_COVER_RATIO
        tot = inv = 0
        try:
            for sp in page.get_texttrace():
                n = len(sp.get("chars") or ())
                tot += n
                if sp.get("type") == 3:
                    inv += n
        except Exception:  # noqa: BLE001 - broken page carries no signal
            tot = inv = 0
        ov_flag = (tot >= OVERLAY_MIN_CHARS
                   and inv / max(tot, 1) >= OVERLAY_INVISIBLE_RATIO)
        if scan_flag:
            scan_like.append(pno)
        if ov_flag:
            overlay_like.append(pno)
        if char_tiers.get(pno) == "trusted" and (scan_flag or ov_flag):
            trusted_scan_like.append(pno)
    fonts: set[str] = set()
    step = max(1, total // FONT_SAMPLE_PAGES)
    for i in range(0, total, step):
        try:
            fonts.update(f[3] for f in doc.get_page_fonts(i))
        except Exception:  # noqa: BLE001
            continue
    doc.close()
    return {
        "total_pages": total,
        "producer": meta.get("producer") or "",
        "creator": meta.get("creator") or "",
        "scan_like_pages": scan_like,
        "overlay_like_pages": overlay_like,
        "trusted_scan_like_pages": trusted_scan_like,
        "gate_pass": not trusted_scan_like,
        "fonts_sample": sorted(fonts)[:FONT_SAMPLE_PAGES],
    }


# ---------------------------------------------------------------------------
# core audit
# ---------------------------------------------------------------------------

def _notify_progress(progress_event, **event) -> None:
    """Send an optional output-only event without changing audit behavior."""
    if progress_event is None:
        return
    try:
        progress_event(event)
    except TypeError:
        try:
            progress_event(**event)
        except Exception as exc:  # noqa: BLE001 - status output is non-critical
            logger.warning("texlayer-audit progress callback failed: %s", exc)
    except Exception as exc:  # noqa: BLE001 - status output is non-critical
        logger.warning("texlayer-audit progress callback failed: %s", exc)


def _completed_page_count(done_pages: dict) -> int:
    """Count pages whose arbitration, if any, has reached a final verdict."""
    return sum(1 for value in done_pages.values()
               if value.get("verdict") != "arbitrating")


def _fingerprint(pdf_path: str, dpi: int) -> str:
    p = Path(pdf_path)
    return f"{p.name}:{p.stat().st_size}:{int(p.stat().st_mtime)}:{dpi}"


def _stream_read(pdf_path: str, pending: list[int], done_pages: dict,
                 dpi: int, workers: int, histogram: dict, counters: dict,
                 arbitrated: dict, mode_counts: dict, arbitrate,
                 flush, say, t0: float, total_targets: int,
                 progress_event=None) -> None:
    """One streaming dual-read phase over `pending` (finalized order).

    v3 refactor of the old inline pipeline: called once for the sample and,
    on escalation/gate-fail/resume, again for the rest. Mutates done_pages,
    histogram, counters ({"ambiguous","overflow"} - MAX_ARBITERS continuity
    across phases), arbitrated and mode_counts in place; persists via flush()
    every FLUSH_EVERY pages so a crash loses at most the last batch.
    """
    import pymupdf as fitz
    import queue as _queue

    # 流水线：渲染单线程（PyMuPDF 非线程安全）→ 有界队列 → OCR 工人池。
    # Rendering and recognition overlap; resource usage is environment-dependent.
    q_render: "_queue.Queue" = _queue.Queue(maxsize=workers * 2)

    def render_producer() -> None:
        try:
            for p in pending:
                q_render.put((p,
                              render_page_png(render_doc[p - 1], dpi=dpi),
                              render_doc[p - 1].get_text() or ""))
        finally:
            q_render.put(None)

    def av_read(png: bytes) -> str:
        return unicodedata.normalize("NFC", ocr_apple.ocr_png(png) or "")

    def judge(p: int, layer: str, ref: str) -> dict:
        entry: dict = {"char_tier": "trusted"}
        skel = cjk_skeleton(layer)
        if len(skel) < MIN_CJK_CHARS:
            entry.update(verdict="n/a", reason=f"cjk<{MIN_CJK_CHARS}")
        elif not ref.strip():
            entry.update(verdict="n/a", reason="av-empty")
        else:
            uni = containment(layer, ref, k=1)   # 主指标：字符集重合（抗 ruby 乱序）
            bi = containment(layer, ref, k=K)    # 次指标：顺序敏感，仅供校准参考
            entry.update(agreement=uni, bigram_agreement=bi,
                         verdict=classify_agreement(uni))
        return entry

    def read_and_judge(p: int, png: bytes, layer: str) -> tuple[int, dict]:
        try:
            ref = av_read(png)
        except Exception as e:  # noqa: BLE001 - engine hiccup -> n/a, not suspect
            logger.warning("av read failed p%s: %s", p, e)
            ref = ""
        return p, judge(p, layer, ref)

    render_doc = fitz.open(pdf_path)
    producer_t = threading.Thread(target=render_producer, daemon=True)
    producer_t.start()

    processed = 0
    ov_start = counters["overflow"]
    arb_futs: list = []
    with ThreadPoolExecutor(max_workers=workers) as ex, \
            ThreadPoolExecutor(max_workers=ARBITER_WORKERS) as arb_ex:
        futs = []
        while True:
            item = q_render.get()
            if item is None:
                break
            futs.append(ex.submit(read_and_judge, *item))
        for fut in as_completed(futs):
            p, entry = fut.result()
            if entry.get("verdict") == "ambiguous":
                if counters["ambiguous"] < MAX_ARBITERS:
                    counters["ambiguous"] += 1
                    entry["verdict"] = "arbitrating"
                    arb_futs.append((p, arb_ex.submit(arbitrate, p)))
                    say(f"texlayer-audit: p{p} ambiguous -> arbiter "
                        f"({counters['ambiguous']}/{MAX_ARBITERS})")
                else:
                    entry["verdict"] = "suspect"
                    entry["reason"] = f"arbitration cap {MAX_ARBITERS} exceeded"
                    counters["overflow"] += 1
            done_pages[p] = entry
            a = entry.get("agreement")
            if a is not None:
                bucket = f"{min(0.95, a // 0.05 * 0.05):.2f}+"
                histogram[bucket] = histogram.get(bucket, 0) + 1
            processed += 1
            _notify_progress(
                progress_event,
                phase="audit",
                done=_completed_page_count(done_pages),
                total=total_targets,
                failed=0,
            )
            if processed % FLUSH_EVERY == 0:
                flush()
            if processed % 32 == 0:
                say(f"texlayer-audit: {processed}/{len(pending)} read this phase, "
                    f"elapsed {time.time() - t0:.0f}s")
    producer_t.join(timeout=5)
    for j, (p, afut) in enumerate(sorted(arb_futs), 1):
        try:
            upd = afut.result()
        except Exception as e:  # noqa: BLE001 - arbiter crash must not kill audit
            logger.warning("arbiter future failed p%s: %s -> suspect", p, e)
            upd = {"verdict": "suspect", "reason": "arbiter-crashed"}
        done_pages[p].update(upd)
        mode = upd.get("mode", "?")
        mode_counts[mode] = mode_counts.get(mode, 0) + 1
        if "arbiter_score" in upd:
            arbitrated[p] = {"score": upd["arbiter_score"],
                             "verdict": upd["verdict"], "mode": mode}
        say(f"texlayer-audit: arbiter {j}/{len(arb_futs)} p{p} -> {upd['verdict']} "
            f"({upd.get('arbiter_score', upd.get('reason', '-'))}) [{mode}]")
        _notify_progress(
            progress_event,
            phase="audit",
            done=_completed_page_count(done_pages),
            total=total_targets,
            failed=0,
        )
    render_doc.close()
    if counters["overflow"] > ov_start:
        say(f"texlayer-audit: {counters['overflow'] - ov_start} pages flagged "
            f"suspect without arbitration (cap {MAX_ARBITERS})")


def audit_pdf(pdf_path: str, extraction_dir: str | None = None,
              dpi: int = 150, workers: int = 4, force: bool = False,
              sampling: str = "auto", sample_size: int | None = None,
              deepen_full: bool = False, detect_only: bool = False,
              progress=None, progress_event=None) -> dict:
    """Dual-read audit; streaming/random-order, resumable, v3 sample-first.

    sampling="auto" (default): scan_signature gate decides. PASS -> dual-read
    a random SAMPLE_SIZE_DEFAULT-page prefix of the shuffled order; clean
    sample finalizes mode="sampled" (unread sections inherit trusted,
    provenance in _texlayer_audit.json + metadata block); ANY suspect in the
    sample escalates to the full sweep; a CJK-thin inconclusive sample also
    finalizes but records an honest note (the full sweep would judge nothing
    there either). Gate FAIL -> straight to the full sweep.
    sampling="off": legacy whole-book sweep. deepen_full=True ignores a cached
    sampled finalize and extends it to the whole book. Partial prior state
    always resumes as a full continuation. detect_only=True returns just the
    gate fingerprint and writes nothing.

    progress: optional callable(str) for detailed heartbeat messages.
    progress_event: optional callable(dict) for compact structured progress.
    Returns the report dict (also persisted next to the extraction dir).
    """
    import pymupdf as fitz

    t0 = time.time()
    pdf_path = str(Path(pdf_path).resolve())
    ext_dir = Path(extraction_dir).resolve() if extraction_dir else Path(pdf_path).parent / "extraction"
    fp = _fingerprint(pdf_path, dpi)

    state: dict = {}
    json_path = ext_dir / AUDIT_JSON_NAME
    if json_path.is_file() and not force:
        try:
            old = json.loads(json_path.read_text(encoding="utf-8"))
            if old.get("fingerprint") == fp:
                state = old
        except (json.JSONDecodeError, OSError):
            state = {}

    say = progress or (lambda s: None)

    _doc = fitz.open(pdf_path)
    total = len(_doc)
    _doc.close()
    char_tiers = {q.page: q.tier for q in scan_pdf(pdf_path)}

    targets = [p for p in range(1, total + 1) if char_tiers.get(p) == "trusted"]
    _notify_progress(
        progress_event, phase="audit", done=0, total=len(targets), failed=0,
        cache_hit=False,
    )

    if detect_only:
        sig = scan_signature(pdf_path, char_tiers)
        tiers: dict[str, int] = {}
        for t in char_tiers.values():
            tiers[t] = tiers.get(t, 0) + 1
        sig["char_tiers"] = tiers
        sig["auditable_pages"] = len(targets)
        sig["gate"] = "digital_native" if sig.pop("gate_pass") else "failed"
        return sig

    done_pages = {int(k): v for k, v in (state.get("pages") or {}).items()}
    pending = [p for p in targets if p not in done_pages]
    _notify_progress(
        progress_event,
        phase="audit",
        done=_completed_page_count(done_pages),
        total=len(targets),
        failed=0,
    )

    old_report = state.get("report") if isinstance(state.get("report"), dict) else None
    if (old_report and old_report.get("mode") == "sampled"
            and not force and not deepen_full):
        say("texlayer-audit: sampled-finalized cache hit "
            "(--full extends to the whole book, --force redoes)")
        _notify_progress(
            progress_event,
            phase="audit",
            done=old_report.get("audited_pages", 0),
            total=len(targets),
            failed=0,
            cache_hit=True,
        )
        return old_report

    rng = random.Random(fp)
    rng.shuffle(pending)  # randomized streaming order: any prefix is a valid sample

    say(f"texlayer-audit: {len(targets)} auditable pages "
        f"({len(done_pages)} already done, {len(pending)} to go)")

    # v3 编排：门控通过先对读一个随机样本相，必要时再续全量相；两相共用
    # 同一套流式管线（_stream_read），done_pages/直方图/仲裁计数跨相累计。
    histogram: dict[str, int] = {}
    mode_counts: dict[str, int] = {}
    arbitrated: dict = {}
    counters = {"ambiguous": 0, "overflow": 0}

    def flush() -> None:
        _flush_state(json_path, fp, dpi, done_pages)

    # Warm up the native adapter in the main thread. Some platform bindings
    # lazily resolve their constants and are not thread-safe on first access.
    if pending:
        try:
            wd = fitz.open(pdf_path)
            ocr_apple.ocr_png(render_page_png(wd[pending[0] - 1], dpi=dpi))
            wd.close()
            logger.info("av warmup done on p%d", pending[0])
        except Exception as e:  # noqa: BLE001
            logger.warning("av warmup failed (continuing): %s", e)

    # 主扫与仲裁重叠：模糊页即时投给仲裁池，仲裁延迟藏进主扫墙钟里；
    # 超出上限的页 FN 倾向直接 suspect。仲裁器本体见 make_arbiter（v2）。
    arb_doc = fitz.open(pdf_path)
    arbitrate = make_arbiter(arb_doc, dpi)

    report_mode = "full"
    sampling_info: dict = {"gate": "skipped", "reason": "disabled"}
    remaining = list(pending)
    fresh = not done_pages

    if sampling != "off" and fresh and pending:
        sig = scan_signature(pdf_path, char_tiers)
        evidence = {"producer": sig["producer"],
                    "scan_like_pages": len(sig["scan_like_pages"]),
                    "overlay_like_pages": len(sig["overlay_like_pages"]),
                    "fonts_sample": sig["fonts_sample"]}
        if not sig["gate_pass"]:
            sampling_info = {"gate": "failed",
                             "evidence": {**evidence,
                                          "trusted_scan_like_pages":
                                              sig["trusted_scan_like_pages"][:20]}}
            say(f"texlayer-audit: digital-native gate FAIL "
                f"({len(sig['trusted_scan_like_pages'])} trusted pages look "
                f"scanned/OCR-overlay) -> full sweep")
        else:
            k = min(sample_size or SAMPLE_SIZE_DEFAULT, len(pending))
            sample = pending[:k]
            remaining = pending[k:]
            say(f"texlayer-audit: digital-native gate PASS -> sampling {k}"
                f"/{len(targets)} auditable pages first")
            _stream_read(pdf_path, sample, done_pages, dpi, workers, histogram,
                         counters, arbitrated, mode_counts, arbitrate,
                         flush, say, t0, len(targets), progress_event)
            suspects = sorted(int(p) for p, v in done_pages.items()
                              if v.get("verdict") == "suspect")
            trusted_n = sum(1 for v in done_pages.values()
                            if v.get("verdict") == "trusted")
            sampling_info = {"gate": "digital_native",
                             "sample_pages": sorted(sample),
                             "evidence": evidence}
            if suspects:
                report_mode = "full_escalated_from_sample"
                sampling_info["escalation_reason"] = \
                    f"suspect pages in sample: {suspects[:8]}"
                say(f"texlayer-audit: suspect in sample {suspects[:8]} "
                    f"-> escalate to full sweep")
            else:
                report_mode = "sampled"
                remaining = []
                note = None
                if trusted_n < SAMPLE_MIN_TRUSTED:
                    note = (f"CJK-thin sample: only {trusted_n}/{k} comparable "
                            f"(rest n/a); a full sweep would judge no more here")
                    sampling_info["note"] = note
                say(f"texlayer-audit: sample clean ({trusted_n} trusted, "
                    f"0 suspect)"
                    f"{(' | ' + note) if note else ''} -> finalized; "
                    f"{len(targets) - len(done_pages)} unread pages inherit "
                    f"trusted (--full to deepen)")
    elif sampling != "off" and not fresh:
        sampling_info = {"gate": "skipped",
                         "reason": "resume: continuing prior partial run"}

    if remaining:
        _stream_read(pdf_path, remaining, done_pages, dpi, workers, histogram,
                     counters, arbitrated, mode_counts, arbitrate,
                     flush, say, t0, len(targets), progress_event)
    arb_doc.close()

    counts = {"trusted": 0, "suspect": 0, "n/a": 0}
    for v in done_pages.values():
        counts[v["verdict"]] = counts.get(v["verdict"], 0) + 1

    sections = []
    suspect_sections = []
    if ext_dir.is_dir():
        for sec in map_sections(ext_dir):
            audited = [done_pages[p] for p in sec["pages"] if p in done_pages]
            sus = [p for p, v in zip(sec["pages"], audited) if v.get("verdict") == "suspect"]
            ratio = (len(sus) / len(audited)) if audited else 0.0
            trigger = bool(audited) and ratio >= SECTION_SUSPECT_RATIO
            item = {"title": sec["title"], "path": str(sec["path"]),
                    "pages": sec["pages"], "audited": len(audited),
                    "suspect_pages": sus, "suspect_ratio": round(ratio, 3),
                    "auto_repair": trigger}
            sections.append(item)
            if trigger:
                suspect_sections.append(item)

    est_repair_sec = sum(len(s["suspect_pages"]) for s in suspect_sections) * REPAIR_EST_SEC_PER_PAGE
    report = {
        "pdf": pdf_path,
        "fingerprint": fp,
        "dpi": dpi,
        "mode": report_mode,
        "total_pages": total,
        "audited_pages": len(done_pages),
        "unread_inherited_trusted":
            max(0, len(targets) - len(done_pages)) if report_mode == "sampled" else 0,
        "counts": counts,
        "corruption_rate": round(counts["suspect"] / max(1, sum(counts.values())), 4),
        "agreement_histogram": dict(sorted(histogram.items(), reverse=True)),
        "arbitrated_count": len(arbitrated),
        "arbiter_modes": mode_counts,
        "sampling": sampling_info,
        "formula_zone": "未审计（独立 OCR 对公式均不可靠；数学内容由视觉链修复时顺带恢复）",
        "sections": [
            {k: (str(v) if isinstance(v, Path) else v) for k, v in it.items()}
            for it in sections],
        "repair_estimate_seconds": est_repair_sec,
        "soft_gate_exceeded": est_repair_sec > REPAIR_SOFT_GATE_SECONDS,
        "elapsed_s": round(time.time() - t0, 1),
    }

    # 公式区测绘（路径 A span，零模型成本）：全书区域地图写 per-PDF sidecar，
    # 节 frontmatter 回写区数。失败只降级，不影响审计结论。
    region_counts: dict[int, int] = {}
    try:
        from . import formula_regions as fr
        regions_data = fr.build_regions(pdf_path, dpi=dpi)
        report["formula_regions_sidecar"] = str(fr.write_sidecar(pdf_path, regions_data))
        for p_str, regs in (regions_data.get("pages") or {}).items():
            region_counts[int(p_str)] = len(regs)
        report["formula_region_total"] = sum(region_counts.values())
        report["formula_zone"] = (
            f"已测绘（{Path(pdf_path).name}.regions.json，"
            f"{report['formula_region_total']} 区；数学内容仍由视觉链修复时顺带恢复）")
    except Exception as e:  # noqa: BLE001
        logger.warning("formula-region mapping failed (non-fatal): %s", e)

    state_out = {"fingerprint": fp, "dpi": dpi, "pages": done_pages,
                 "arbitrated": arbitrated, "report": report}
    if ext_dir.is_dir():
        ext_dir.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(state_out, ensure_ascii=False, indent=1),
                             encoding="utf-8")
        _write_metadata_block(ext_dir, report)
        for sec in sections:
            fm_val = "trusted"
            if sec["audited"] == 0:
                fm_val = "trusted" if report_mode == "sampled" else "unaudited"
            elif sec["auto_repair"]:
                fm_val = f"suspect({len(sec['suspect_pages'])}/{sec['audited']} 页)"
            n_regions = sum(region_counts.get(p, 0) for p in sec["pages"])
            if sec["audited"] == 0 and report_mode != "sampled":
                fq_val = "未审计"
            else:
                # 区域地图按全书 PDF 生成，抽样定案的未读节同样有图可查
                fq_val = f"已测绘 {n_regions} 区"
            _set_frontmatter_key(Path(sec["path"]), {
                _FM_KEY: fm_val,
                "公式区": fq_val,
            })
    _notify_progress(
        progress_event,
        phase="audit",
        done=_completed_page_count(done_pages),
        total=len(targets),
        failed=0,
    )
    return report


def _flush_state(json_path: Path, fp: str, dpi: int, done_pages: dict) -> None:
    """崩溃保险：把已定稿的页刷盘。arbitrating 中间态不写——恢复时这些页
    重跑主扫（约 1s/页 AV 重读），换来任何时刻崩溃最多丢 FLUSH_EVERY 页。"""
    snap = {str(k): v for k, v in done_pages.items()
            if v.get("verdict") != "arbitrating"}
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps({"fingerprint": fp, "dpi": dpi, "pages": snap},
                   ensure_ascii=False, indent=1),
        encoding="utf-8")


def _write_metadata_block(ext_dir: Path, report: dict) -> None:
    meta_name = os.environ.get("PDFX_METADATA_FILE", "00_book_metadata.md").strip()
    meta = ext_dir / (meta_name or "00_book_metadata.md")
    if not meta.is_file():
        return
    text = meta.read_text(encoding="utf-8")
    samp = report.get("sampling") or {}
    mode = report.get("mode", "full")
    block_lines = [
        META_BEGIN,
        "",
        "## Text-Layer Semantic Audit",
        "",
        f"- **Mode**: {mode}",
        f"- **Audited**: pages {report['audited_pages']}/{report['total_pages']}, "
        f"dpi={report['dpi']}, elapsed {report['elapsed_s']}s",
        f"- **Verdicts**: trusted={report['counts'].get('trusted', 0)}, "
        f"suspect={report['counts'].get('suspect', 0)}, "
        f"n/a={report['counts'].get('n/a', 0)} "
        f"(corruption rate {report['corruption_rate']:.1%})",
    ]
    if samp.get("gate") == "digital_native":
        ev = samp.get("evidence", {})
        outcome = ("FINALIZED — 其余未读页按文档级判 trusted"
                   if mode == "sampled"
                   else f"escalated to full ({samp.get('escalation_reason', '?')})")
        block_lines.append(
            f"- **Sampling**: {len(samp.get('sample_pages', []))} 页对读，{outcome}; "
            f"门控证据 producer={ev.get('producer') or '?'}, "
            f"scan-like pages={ev.get('scan_like_pages')}, "
            f"overlay-like pages={ev.get('overlay_like_pages')}"
            + (f"; {samp['note']}" if samp.get("note") else ""))
    elif samp.get("gate") == "failed":
        block_lines.append(
            "- **Sampling**: 门控未过（trusted 页带扫描/OCR 覆盖层特征）→ 全量对读")
    block_lines += [
        f"- **公式区**: {report['formula_zone']}",
        f"- **Auto-repair candidates** (≥{int(SECTION_SUSPECT_RATIO * 100)}% suspect): "
        f"{sum(1 for s in report['sections'] if s['auto_repair'])} sections, "
        f"estimated repair ≈ {report['repair_estimate_seconds'] // 60} min",
        "",
        "| Section | Audited | Suspect pages | Verdict |",
        "|---|---|---|---|",
    ]
    for s in report["sections"]:
        if s["audited"]:
            verd = ("trusted" if not s["suspect_ratio"]
                    else f"suspect({len(s['suspect_pages'])}/{s['audited']})")
        else:
            verd = "trusted*" if mode == "sampled" else "unaudited"
        block_lines.append(
            f"| {s['title'][:48]} | {s['audited']} | "
            f"{','.join(map(str, s['suspect_pages'][:8])) or '-'} | {verd} |")
    if mode == "sampled":
        block_lines += ["", "* 未逐页对读：全书通过数字原生门控且抽样干净，"
                            "按书级判定记 trusted（事实源 _texlayer_audit.json "
                            "的 sampling 字段；--full 可扩成全量）"]
    block_lines += ["", META_END, ""]
    block = "\n".join(block_lines)

    if META_BEGIN in text and META_END in text:
        pre = text.split(META_BEGIN, 1)[0]
        post = text.split(META_END, 1)[1]
        meta.write_text(pre + block + post.lstrip("\n"), encoding="utf-8")
    else:
        meta.write_text(text.rstrip("\n") + "\n\n" + block, encoding="utf-8")


def repair_plan(report: dict) -> dict:
    """What the orchestrator needs to decide on auto-repair (soft gate)."""
    secs = [s for s in report["sections"] if s["auto_repair"]]
    return {
        "sections": [{"title": s["title"], "path": s["path"],
                      "suspect_pages": s["suspect_pages"]} for s in secs],
        "page_count": sum(len(s["suspect_pages"]) for s in secs),
        "estimate_seconds": report["repair_estimate_seconds"],
        "needs_user_consent": report["soft_gate_exceeded"],
        "note": "修复由调用方的视觉重识别流程执行；"
                "修完用本模块 audit_pdf 复检该节，一致率未升则如实标注人工待审",
    }
