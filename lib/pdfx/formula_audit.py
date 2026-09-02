"""Formula-region correctness audit (P0 of the formula-audit plan).

Why this exists: formula regions are deliberately EXCLUDED from the
texlayer semantic audit (both engines misread math, each in its own way —
AV turns σ² into `0”`), so "the text layer is trusted" says nothing about
whether a formula region's extracted plain text is CORRECT. scan_math.py
only catches *ambiguous* plain-text math (`e^x^2`, `1/2x`, …); it cannot
see "spelled-plausibly-but-wrong" formulas (`f（2;3）` where the page shows
f(x,y)). This module audits every formula region per page and persists a
`.faudit.json` sidecar — the single source of truth a consumer checks
before quoting a formula.

Three signal layers (per formula region):
  L1  ambiguity patterns (same 5 families as scan_math.py — imported,
      not duplicated): `e^x^2`, `x_n^2`, `1/2x`, `sin x+1`, `√x+1`.
  L2  text-layer vs layout geometry: when a page has layout-source regions
      (path B, pp-doclayout) the text layer is checked for agreement — a
      layout box that has no readable/overlapping layer line is a
      "couldn't verify" signal (scanned/hybrid books). On trusted pages
      with span-source regions the layer is authoritative.
  L3  semantic consistency (text-only heuristics, no model): CJK/wide
      chars that leak into a pure math region (`f（2;3）`, `g(jc)`), and
      structurally impossible math (nested fractions via slash chains, or
      a run that mixes operators with no operand). Real L3 semantic
      judgment ("spelled plausibly but wrong") is deferred to a lazy
      per-consumption hook (see the plan) that writes verdicts back into
      this sidecar's cache.

Verdict per region (5 states):
  ok          - L1 clean, L2 consistent, L3 passed (or trivially empty)
  suspect     - L1 or L2 or L3 hit
  unverified  - scanned book (no usable text layer): nothing to judge; the
                region goes straight into the repair chain (llm-ocr-refresh)
  pending_l3  - has a usable layer, L1/L2 pass, L3 not yet judged — the
                consumption hook runs L3 for THIS section on demand and
                caches the result back
  empty       - region contains no text at all (blank crop)

Schema (.faudit.json, aligned with _texlayer_audit.json):
  {
    "fingerprint": "<pdf>:<size>:<mtime>:<dpi>",
    "dpi": 150,
    "generated_at": "...",
    "known_limits": [...],
    "pages": {
      "12": {
        "page_verdict": "ok|suspect|unverified|pending_l3|empty|mixed",
        "verdicts": [
          {"bbox_pt": [...], "class": "...", "source": "span|layout",
           "text": "...", "verdict": "ok|suspect|unverified|pending_l3|empty",
           "signals": ["L1:exp_chain", "L2:no_layer_match", "L3:cjk_in_math", ...]}
        ]
      }
    },
    "skipped": {"13": "layout_unavailable"},
    "report": {
      "sections": [
        {"title": "...", "path": "<text.md abs path>",
         "pages": [..], "faudit_ok": N, "faudit_suspect": N,
         "faudit_unverified": N, "faudit_pending_l3": N, "faudit_empty": N,
         "verdict": "ok|suspect|unverified|pending_l3"}
      ]
    },
    "summary": {...}
  }

Repair invariant: "repair complete == every region of the section is ok".
Consumer hooks can refuse to serve a section whose report verdict is anything
but ok.
"""

from __future__ import annotations

import json
import hashlib
import logging
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from . import scan_math
from .formula_regions import (
    KNOWN_LIMITS,
    fingerprint,
    load_sidecar,
    regions_for_page,
    sidecar_path,
)
from .quality import scan_pdf

logger = logging.getLogger(__name__)

FAUDIT_SUFFIX = ".faudit.json"
SIDE_CARRIER = "split_pdfs"   # retained artifact-name constant for integrations
_FAUDIT_FM_KEY = "公式审计"
L3_RULE_VERSION = 1


# ---------------------------------------------------------------------------
# L1: ambiguity (imported from scan_math so a signal is never duplicated)
# ---------------------------------------------------------------------------

def l1_signals(text: str) -> list[str]:
    """Return the scan_math ambiguity family names present in `text`.

    LaTeX-delimited segments are masked first (well-formed $...$ never fires).
    """
    masked = scan_math.mask_latex(text or "")
    if masked == text and "$" in text:
        return []
    out = []
    for name, rex in scan_math.PATTERNS:
        if rex.search(masked):
            out.append(f"L1:{name}")
    return out


# ---------------------------------------------------------------------------
# L3: text-only semantic consistency heuristics (cheap, deterministic)
# ---------------------------------------------------------------------------

# full-width / CJK punctuation and Han/kana that should not sit INSIDE a
# pure math region (page images show latin math even in Japanese books).
_WIDE_PUNCT_RE = re.compile(r"[\u3000-\u303F\uFF00-\uFF60\u3001\u3002]")
_CJK_CHAR_RE = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF\u3040-\u30FF\uF900-\uFAFF]")

# classic extraction corruptions that are 'legal-looking but wrong'
_LATEX_CMD_LEAK_RE = re.compile(r"\\[A-Za-z]{2,}")
_LIMIT_UNDER_RE = re.compile(r"[\w)]_\[?\{?\w")       # x_{n}, a_1 stuck as subscript
_SLASH_CHAIN_RE = re.compile(r"[^/]{2}/\d+/\d+|[^/]\d+/\d+/\d+")   # 1/2/3 nested fracs
_DOUBLE_OP_RE = re.compile(r"[×÷=+\-−±]\s*[×÷=+\-−±]")             # = =, +× adjacent
_OPERATOR_ONLY_RE = re.compile(r"^[\s×÷=+\-−±]+$")                 # region is just '='/'+-'


def l3_signals(text: str) -> list[str]:
    """Cheap deterministic semantic-consistency hits (see module docstring).

    These are FIRING signals (region → suspect), not a full L3 judgment —
    the full "spelled plausibly but wrong" read is the lazy consumption hook.
    Deliberately conservative: region bboxes get clipped at line boundaries,
    so a lone `=` or `= 3` at a region edge is NOT corruption — only double
    operators, operator-only regions, leaked CJK/punct and stuck subscripts.
    """
    t = text or ""
    sigs = []
    if _WIDE_PUNCT_RE.search(t):
        sigs.append("L3:wide_punct_in_math")
    if _CJK_CHAR_RE.search(t):
        sigs.append("L3:cjk_in_math")
    if _LATEX_CMD_LEAK_RE.search(t):
        sigs.append("L3:latex_cmd_leak")
    if _LIMIT_UNDER_RE.search(t) and "_{" not in t:
        sigs.append("L3:stuck_subscript")
    if _SLASH_CHAIN_RE.search(t):
        sigs.append("L3:slash_chain")
    if _DOUBLE_OP_RE.search(t):
        sigs.append("L3:double_operator")
    if _OPERATOR_ONLY_RE.search(t):
        sigs.append("L3:operator_only")
    return sigs


# ---------------------------------------------------------------------------
# L2: text layer vs layout geometry
# ---------------------------------------------------------------------------

def _layer_text_in(page, rect) -> str:
    """Text-layer content clipped to a pt rect (whitespace-normalized)."""
    try:
        return " ".join(page.get_text(clip=rect).split())
    except Exception:  # noqa: BLE001 - broken clip is a no-signal miss
        return ""


def _l2_check(page, region: dict, region_text: str, layout_present: bool) -> tuple[str, str | None]:
    """Layer-vs-geometry consistency.

    Returns (verdict_piece, signal). Rules:
      - layout-source region on this page: the layout box says 'math lives
        here'; we need the text layer to have a matching line inside the box.
        No match → unverified (this is the scanned/hybrid-book signal that
        drives the repair chain). A match → the region passes L2.
      - span-source region (derived FROM the layer): the layer itself is the
        source of the region, so L2 is inherently consistent → None (pass).
    """
    if region.get("source") == "layout":
        box_text = _layer_text_in(page, tuple(region["bbox_pt"]))
        core = re.sub(r"[\s()\[\]]+", "", region_text or "")
        if not core:
            return "empty", None
        layer_core = re.sub(r"[\s()\[\]]+", "", box_text or "")
        overlap = core[:24] in layer_core or (
            bool(core) and bool(layer_core) and
            len(set(core[:24]) & set(layer_core)) / len(set(core[:24])) >= 0.6)
        if not overlap:
            return "unverified", "L2:no_layer_match"
        return None, None
    return None, None  # span-source: layer is the source of truth


def _segments(text: str) -> list[str]:
    """Split a region's joined text back into its logical lines.

    formula_regions._cluster_lines joins a cluster's line texts with
    `" / "`. A literal `/` between two words is NOT a fraction (it is the
    join separator), so L1/L3 must scan each segment independently or
    `A / Show that` would false-positive as `A/S...`.
    """
    return [s.strip() for s in (text or "").split(" / ") if s.strip()]


# ---------------------------------------------------------------------------
# main per-region judgment
# ---------------------------------------------------------------------------

def _judge_region(page, region: dict, page_tier: str, layout_present: bool,
                  llm_ocr_ok: bool = False) -> dict:
    """One region → {verdict, signals}. page_tier is the quality tier."""
    # llm_ocr acceptance (`llm_ocr: true` == repair complete
    # for the WHOLE section, no residue). When the section's md was
    # re-recognised by the vision chain, the consumed content no longer comes
    # from the PDF text layer judged here — so every region passes with an
    # evidence tag. Without this the repair chain cannot converge on digital
    # (trusted-layer) books: rewriting text.md never changes the layer, so
    # Keep signal definitions stable across different document collections.
    if llm_ocr_ok:
        return {"verdict": "ok", "signals": ["llm_ocr_repaired"]}

    text = (region.get("text") or "").strip()
    signals: list[str] = []

    # Image-only pages (no usable layer) cannot be judged by layer evidence.
    # A layout box on such a page still says "math lives here" — the region is
    # real but its extracted text is not trustworthy → unverified (drives the
    # repair chain). A span-region on a no-layer page means the sidecar was
    # built from garbage geometry → empty.
    if page_tier in ("empty", "untrusted") and not text:
        if region.get("source") == "layout":
            return {"verdict": "unverified", "signals": signals}
        return {"verdict": "empty", "signals": signals}

    # layer-based: L1 + L3 + (L2 for layout boxes). Scan per segment so the
    # ` / ` join separator never masquerades as a fraction.
    for seg in _segments(text):
        signals += l1_signals(seg)
        signals += l3_signals(seg)

    v2, sig2 = _l2_check(page, region, text, layout_present)
    if sig2:
        signals.append(sig2)
    signals = sorted(set(signals))

    if signals:
        verdict = "suspect"
    elif v2 == "unverified":
        verdict = "unverified"
    elif not text:
        verdict = "empty"
    else:
        verdict = "pending_l3"

    return {"verdict": verdict, "signals": signals}


def _aggregate(verdicts: list[str]) -> str:
    """Page/section-level aggregation with the same priority order as the plan.

    ok < pending_l3 < unverified < suspect  (suspect dominates everything).
    """
    order = {"ok": 0, "empty": 1, "pending_l3": 2, "unverified": 3, "suspect": 4}
    if not verdicts:
        return "ok"
    agg = max(verdicts, key=lambda v: order.get(v, 0))
    return "ok" if agg == "ok" else agg


def region_fingerprint(pdf_fingerprint: str, page: int | str, entry: dict) -> str:
    """Stable cache key for a formula-region judgment.

    L3 decisions are evidence about the current PDF region and current text
    layer, not permanent facts.  Keeping the key local to the sidecar lets a
    forced audit preserve only decisions that still describe the same input.
    """
    payload = {
        "pdf": pdf_fingerprint,
        "page": int(page),
        "bbox_pt": [round(float(v), 3) for v in entry.get("bbox_pt") or []],
        "text": entry.get("text") or "",
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def l3_risk(entry: dict) -> str:
    """Classify a pending-L3 formula without using an LLM.

    Only intentionally boring formulas are low risk.  Any notation whose
    meaning can change through a missed glyph must be visually sampled.
    """
    text = (entry.get("text") or "").strip()
    if len(text) > 40:
        return "high"
    if re.search(r"[\^_/]|\\(?:frac|sqrt|int|sum|prod|begin|end|matrix|pmatrix|det)|[−-]|[1lIO0]", text):
        return "high"
    return "low"


def _refresh_page_aggregates(faudit: dict) -> None:
    """Recompute aggregate fields after an in-place L3 decision update."""
    counts: Counter = Counter()
    for page in (faudit.get("pages") or {}).values():
        verdicts = page.get("verdicts") or []
        if verdicts:
            page["page_verdict"] = _aggregate([v.get("verdict", "ok") for v in verdicts])
            counts.update(v.get("verdict", "ok") for v in verdicts)
        else:
            counts[page.get("page_verdict", "ok")] += 1
    for section in faudit.get("report", {}).get("sections") or []:
        pages = [str(p) for p in section.get("pages") or []]
        values = [faudit["pages"][p].get("page_verdict", "ok") for p in pages if p in faudit.get("pages", {})]
        section["faudit_ok"] = values.count("ok")
        section["faudit_suspect"] = values.count("suspect")
        section["faudit_unverified"] = values.count("unverified")
        section["faudit_pending_l3"] = values.count("pending_l3")
        section["faudit_empty"] = values.count("empty")
        section["verdict"] = _aggregate(values) if values else "ok"
    faudit.setdefault("summary", {})["verdict_counts"] = dict(counts)


def apply_l3_checks(pdf_path: str, checks: list[dict]) -> dict:
    """Persist deterministic or worker-backed L3 outcomes in `.faudit.json`.

    A check identifies a region by its current fingerprint.  Stale worker
    output is rejected instead of being applied to a changed PDF/text layer.
    OCR text is deliberately not stored here; it belongs in a transient unit
    file until a repair is atomically accepted.
    """
    faudit = load_faudit(pdf_path)
    if faudit is None:
        raise ValueError(f"missing faudit sidecar for {pdf_path}")
    applied = 0
    stale = 0
    for check in checks:
        page = str(check.get("page"))
        wanted = check.get("region_fingerprint")
        for entry in (faudit.get("pages", {}).get(page, {}).get("verdicts") or []):
            current = region_fingerprint(faudit["fingerprint"], page, entry)
            if current != wanted:
                continue
            status = check.get("status")
            if status == "passed":
                entry["verdict"] = "ok"
            elif status == "escalated":
                entry["verdict"] = "suspect"
            else:
                raise ValueError(f"unsupported L3 status: {status!r}")
            entry["l3_check"] = {
                "status": status,
                "method": check.get("method", "risk-sample"),
                "rule_version": L3_RULE_VERSION,
                "region_fingerprint": current,
                "checked_at": datetime.now().isoformat(timespec="seconds"),
            }
            applied += 1
            break
        else:
            stale += 1
    _refresh_page_aggregates(faudit)
    faudit_path(pdf_path).write_text(json.dumps(faudit, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"applied": applied, "stale": stale}


def pending_l3_plan(pdf_path: str, text_layer: str | None = None) -> dict:
    """Return deterministic L3 work for one sidecar without rendering pages.

    Low-risk regions can be accepted locally.  High-risk regions are sampled
    in a stable fingerprint-derived order; one failed sample escalates the
    rest of that PDF instead of silently passing them.
    """
    faudit = load_faudit(pdf_path)
    if faudit is None:
        raise ValueError(f"missing faudit sidecar for {pdf_path}")
    if text_layer not in {"trusted", "washable"}:
        return {"low_risk": [], "sample": [], "high_risk": [], "eligible": False}
    low_risk, high_risk = [], []
    for page, page_data in (faudit.get("pages") or {}).items():
        for entry in page_data.get("verdicts") or []:
            if entry.get("verdict") != "pending_l3":
                continue
            item = {
                "page": int(page),
                "bbox_pt": entry.get("bbox_pt"),
                "region_fingerprint": region_fingerprint(faudit["fingerprint"], page, entry),
            }
            (low_risk if l3_risk(entry) == "low" else high_risk).append(item)
    high_risk.sort(key=lambda item: item["region_fingerprint"])
    return {
        "low_risk": low_risk,
        "sample": high_risk[:3],
        "high_risk": high_risk,
        "eligible": True,
    }


# ---------------------------------------------------------------------------
# source-section mapping (derived PDF <-> source document pages)
# ---------------------------------------------------------------------------

def map_section_for_pdf(pdf_path: str, extraction_dir: Path) -> dict | None:
    """Find the one source section that belongs to this derived PDF.

    A section directory name is matched as a suffix of the PDF stem. Match the
    longest such suffix so repeated names never misbind.

    Returns {title, path, pages} with pages = the derived PDF's own page
    coordinates (1..N) — the keys the faudit sidecar uses.
    """
    stem = Path(pdf_path).stem
    best = None
    for md in sorted(p for p in extraction_dir.rglob("*.md")
                     if p.name == "text.md"):
        if not md.read_text(encoding="utf-8").startswith("---"):
            continue
        name = md.parent.name
        if stem.endswith(name) and (best is None or len(name) > len(best[0])):
            best = (name, md)
    if best is None:
        return None
    _name, md = best
    text = md.read_text(encoding="utf-8")
    end = text.find("\n---", 3)
    fm = text[3:end] if end != -1 else ""
    title_m = re.search(r'^title:\s*"(.*)"\s*$', fm, re.M)
    phys_m = re.search(r'^PDF物理页码:\s*"(.*)"\s*$', fm, re.M)
    if not title_m:
        return None
    return {"title": title_m.group(1), "path": str(md),
            "pages": None,  # filled by the caller (split-PDF coordinates)
            "printed_pages": _parse_page_range(phys_m.group(1)) if phys_m else []}


def _parse_page_range(raw: str) -> list[int]:
    """'44 〜 53' / 'p.8-15' / '12' -> [44..53] etc (physical pages)."""
    nums = [int(n) for n in re.findall(r"\d+", raw or "")]
    if not nums:
        return []
    if len(nums) >= 2 and nums[1] >= nums[0]:
        lo, hi = nums[0], min(nums[1], nums[0] + 400)
        return list(range(lo, hi + 1))
    return [nums[0]]


# ---------------------------------------------------------------------------
# sidecar I/O
# ---------------------------------------------------------------------------

def faudit_path(pdf_path: str) -> Path:
    return Path(pdf_path).with_suffix(FAUDIT_SUFFIX)


def load_faudit(pdf_path: str) -> dict | None:
    fp = faudit_path(pdf_path)
    if not fp.is_file():
        return None
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


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
            logger.warning("formula-audit progress callback failed: %s", exc)
    except Exception as exc:  # noqa: BLE001 - status output is non-critical
        logger.warning("formula-audit progress callback failed: %s", exc)


def section_verdict(faudit: dict | None, section_pages: list[int]) -> str | None:
    """O(1) section aggregate from the sidecar's report (or None if absent)."""
    if not faudit:
        return None
    for s in faudit.get("report", {}).get("sections", []):
        if s.get("pages") == section_pages or (
                s.get("path") and section_pages and
                sorted(s["pages"]) == sorted(section_pages)):
            return s.get("verdict")
    return None


# ---------------------------------------------------------------------------
# core audit
# ---------------------------------------------------------------------------

def audit_pdf(pdf_path: str, extraction_dir: str | None = None,
              dpi: int = 150, force: bool = False,
              use_layout: bool = False, progress=None,
              progress_event=None) -> dict:
    """Run the P0 audit on one split PDF; persist `<pdf>.faudit.json`.

    Regions come from the existing `<pdf>.regions.json` sidecar
    (formula_regions.py); if the sidecar is missing or its fingerprint is
    stale it is rebuilt first (span path, zero model cost). When use_layout
    is set, untrusted/empty pages get the layout path added on rebuild.

    extraction_dir: textbook extraction dir (defaults to
    `<book>/extraction`). When provided, `report.sections[]` aggregates per
    section so consumer hooks get O(1) lookup. When absent, sections is
    empty and only per-page verdicts are produced.

    progress: optional callable(str) for detailed heartbeat messages.
    progress_event: optional callable(dict) for compact structured progress.
    """
    import pymupdf as fitz

    pdf_path = str(Path(pdf_path).resolve())
    fp = fingerprint(pdf_path, dpi)
    say = progress or (lambda s: None)

    doc0 = fitz.open(pdf_path)
    total = len(doc0)
    doc0.close()
    _notify_progress(
        progress_event, phase="audit", done=0, total=total, failed=0,
        cache_hit=False,
    )

    # 1. regions sidecar (rebuild only when missing/stale, or when the
    #    caller asks for layout but the cached sidecar was built without it)
    side = load_sidecar(pdf_path)
    need_layout = use_layout and side is not None
    if side is None or side.get("fingerprint") != fp or need_layout:
        if side is not None and side.get("fingerprint") == fp:
            # cached but we were asked for layout: rebuild so untrusted/empty
            # pages get path-B regions (previous bug: use_layout never applied
            # when the sidecar was reused from cache)
            say(f"formula-audit: rebuilding regions sidecar with --layout for {Path(pdf_path).name}")
        else:
            say(f"formula-audit: rebuilding regions sidecar for {Path(pdf_path).name}")
        from . import formula_regions as fr
        side = fr.build_regions(pdf_path, use_layout=use_layout, dpi=dpi,
                                progress=lambda s: say(f"  {s}"))
        fr.write_sidecar(pdf_path, side)

    # 2. cached faudit (resume)
    faudit = load_faudit(pdf_path)
    if faudit and faudit.get("fingerprint") == fp and not force:
        _notify_progress(
            progress_event, phase="audit", done=total, total=total, failed=0,
            cache_hit=True,
        )
        return faudit
    old_l3_checks = {}
    if faudit and faudit.get("fingerprint") == fp:
        for page, page_data in (faudit.get("pages") or {}).items():
            for entry in page_data.get("verdicts") or []:
                check = entry.get("l3_check")
                if check and check.get("region_fingerprint"):
                    old_l3_checks[check["region_fingerprint"]] = check

    doc = fitz.open(pdf_path)
    tiers = {q.page: q.tier for q in scan_pdf(pdf_path)}

    # llm_ocr acceptance: a scanned page (empty/untrusted + images) is
    # "cannot judge from the layer" — BUT if its section's text.md carries
    # `llm_ocr: true`, the page's content has already been re-recognised by
    # the LLM vision chain (llm-ocr-refresh) with formula fidelity
    # guarantees. That is the repair-complete state for scanned books:
    # mark the page ok with an `llm_ocr` evidence tag instead of unverified,
    # so the repair chain converges (was: forever unverified → never ok).
    llm_ocr_mds: set[Path] = set()
    if extraction_dir:
        sec0 = map_section_for_pdf(pdf_path, Path(extraction_dir).resolve())
        if sec0 is not None:
            p = Path(sec0["path"])
            if _frontmatter_flag(p, "llm_ocr"):
                llm_ocr_mds.add(p)
    else:
        # kakomon: same-dir same-name .md is the target (no extraction_dir)
        p = Path(pdf_path).with_suffix(".md")
        if _frontmatter_flag(p, "llm_ocr"):
            llm_ocr_mds.add(p)

    pages_out: dict[str, dict] = {}
    skipped: dict[str, str] = {}
    counts: Counter = Counter()

    for pno in range(1, total + 1):
        page = doc[pno - 1]
        regions = regions_for_page(side, pno)
        page_tier = tiers.get(pno, "empty")

        if not regions:
            # No geometry to judge. A scanned page (empty/untrusted, content
            # lives in images) is still "can't judge" → unverified at the PAGE
            # level so the section aggregate surfaces into the repair chain.
            # A page with no formula regions at all is simply ok.
            n_images = len(page.get_images(full=True))
            if page_tier in ("empty", "untrusted") and n_images > 0:
                if llm_ocr_mds:
                    pages_out[str(pno)] = {"page_verdict": "ok",
                                           "verdicts": [],
                                           "page_tier": page_tier,
                                           "images": n_images,
                                           "llm_ocr": True}
                    counts["ok"] += 1
                else:
                    pages_out[str(pno)] = {"page_verdict": "unverified",
                                           "verdicts": [],
                                           "page_tier": page_tier,
                                           "images": n_images}
                    counts["unverified"] += 1
            else:
                pages_out[str(pno)] = {"page_verdict": "ok", "verdicts": []}
                counts["ok"] += 1
            if side and str(pno) in (side.get("skipped") or {}):
                skipped[str(pno)] = side["skipped"][str(pno)]
            _notify_progress(
                progress_event, phase="audit", done=pno, total=total, failed=0,
            )
            continue

        layout_present = any(r.get("source") == "layout" for r in regions)
        verdicts = []
        for region in regions:
            res = _judge_region(page, region, page_tier, layout_present,
                                llm_ocr_ok=bool(llm_ocr_mds))
            entry = {"bbox_pt": region["bbox_pt"], "class": region["class"],
                     "source": region.get("source"),
                     "text": (region.get("text") or "")[:120],
                     "verdict": res["verdict"],
                     "signals": res["signals"]}
            old_check = old_l3_checks.get(region_fingerprint(fp, pno, entry))
            if old_check and old_check.get("status") == "passed" and entry["verdict"] == "pending_l3":
                entry["verdict"] = "ok"
                entry["l3_check"] = old_check
            elif old_check and old_check.get("status") == "escalated" and entry["verdict"] == "pending_l3":
                entry["verdict"] = "suspect"
                entry["l3_check"] = old_check
            verdicts.append(entry)
            counts[entry["verdict"]] += 1
        pages_out[str(pno)] = {
            "page_verdict": _aggregate([v["verdict"] for v in verdicts]),
            "verdicts": verdicts,
            "llm_ocr": True if llm_ocr_mds else None,
        }
        say(f"formula-audit: p{pno} {pages_out[str(pno)]['page_verdict']} "
            f"({len(verdicts)} regions)")
        _notify_progress(
            progress_event, phase="audit", done=pno, total=total, failed=0,
        )

    doc.close()

    # 3. section aggregation (the one extracted section this PDF maps to)
    sections = []
    if extraction_dir:
        sec = map_section_for_pdf(pdf_path, Path(extraction_dir).resolve())
        if sec is not None:
            sec_pages = sorted(int(p) for p in pages_out)
            vcounts = Counter(pages_out[str(p)]["page_verdict"] for p in sec_pages)
            vset = list(vcounts.elements())
            verdict = _aggregate(vset) if vset else "ok"
            sections.append({
                "title": sec["title"], "path": str(sec["path"]),
                "pages": sec_pages,
                "faudit_ok": vcounts.get("ok", 0),
                "faudit_suspect": vcounts.get("suspect", 0),
                "faudit_unverified": vcounts.get("unverified", 0),
                "faudit_pending_l3": vcounts.get("pending_l3", 0),
                "faudit_empty": vcounts.get("empty", 0),
                "verdict": verdict,
            })

    data = {
        "fingerprint": fp,
        "dpi": dpi,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "known_limits": list(KNOWN_LIMITS),
        "pages": pages_out,
        "skipped": skipped,
        "report": {
            "sections": sections,
        },
        "summary": {
            "regions": sum(counts.values()),
            "verdict_counts": dict(counts),
            "pages_audited": len(pages_out),
            "sections": len(sections),
        },
    }
    fp_path = faudit_path(pdf_path)
    fp_path.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                       encoding="utf-8")
    say(f"formula-audit: wrote {fp_path.name} "
        f"({sum(counts.values())} regions, {len(sections)} sections)")
    return data


def project_frontmatter(pdf_path: str, extraction_dir: Path) -> dict:
    """Project section aggregate verdicts into each section's text.md frontmatter.

    Same mechanism as 文字层审计 projection (texlayer_audit._set_frontmatter_key).
    Re-run after a repair to refresh. Returns {section_path: value_written}.
    """
    faudit = load_faudit(pdf_path)
    if not faudit:
        return {}
    written: dict = {}
    for s in faudit.get("report", {}).get("sections", []):
        md = Path(s["path"])
        if not md.is_file():
            continue
        verdict = s["verdict"]
        if verdict == "ok":
            val = "ok"
        elif verdict == "suspect":
            val = f"suspect({s['faudit_suspect']} 区)"
        elif verdict == "unverified":
            val = f"unverified({s['faudit_unverified']} 区)"
        elif verdict == "pending_l3":
            val = f"pending_l3({s['faudit_pending_l3']} 区)"
        else:
            val = verdict
        _set_frontmatter_key(md, {_FAUDIT_FM_KEY: val})
        written[str(md)] = val
    return written


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


def _frontmatter_flag(md_path: Path, key: str) -> bool:
    """True when the md frontmatter contains `key: true` (e.g. `llm_ocr: true`)."""
    try:
        text = md_path.read_text(encoding="utf-8")
    except OSError:
        return False
    if not text.startswith("---"):
        return False
    end = text.find("\n---", 3)
    if end == -1:
        return False
    block = text[3:end]
    m = re.search(rf"^{re.escape(key)}:\s*(.+)$", block, re.M)
    if not m:
        return False
    return m.group(1).strip().lower() in ("true", '"true"', "'true'", "yes")
