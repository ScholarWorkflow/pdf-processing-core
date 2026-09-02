"""pdfx command line: quality / regions / scan-math / extract / calibrate / toc-segments / restore-toc / inject-toc / check-toc.

Run:
  uv run --with pymupdf python lib/pdfx/cli.py <cmd> ...
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

# Keep the historical direct-script entry point working without depending on
# the caller's current directory; installed users use the pdfx console script.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pdfx import clean as clean_mod
from pdfx import quality as quality_mod
from pdfx import toc as toc_mod
from pdfx.extract import DEFAULT_ENGINES, extract_pdf
from pdfx.status import StatusReporter

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("pdfx.cli")


def _short_reason(exc: Exception) -> str:
    return " ".join(str(exc).split())[:160] or type(exc).__name__


def _formula_audit_verdict(report: dict) -> str:
    order = {"ok": 0, "empty": 1, "pending_l3": 2, "unverified": 3, "suspect": 4}
    values = [
        page.get("page_verdict", "ok")
        for page in (report.get("pages") or {}).values()
        if isinstance(page, dict)
    ]
    return max(values, key=lambda value: order.get(value, 0)) if values else "ok"


def _emit(obj: dict, as_json: bool):
    if as_json:
        print(json.dumps(obj, ensure_ascii=False, indent=2))
    return obj


def cmd_quality(args):
    qualities = quality_mod.scan_pdf(args.pdf)
    report = {
        "pdf": args.pdf,
        "thresholds": {
            "garble": quality_mod.GARBLE_THRESHOLD,
            "cjk_space_rate": quality_mod.CJK_SPACE_THRESHOLD,
            "empty_chars": quality_mod.EMPTY_CHARS,
            "calibrated": "representative synthetic benchmark",
        },
        "summary": quality_mod.summarize(qualities),
        "pages": [q.to_dict() for q in qualities],
    }
    _emit(report, args.json)


def cmd_regions(args):
    from pdfx import formula_regions as fr

    pages = fr.parse_page_spec(args.pages)
    data = fr.build_regions(args.pdf, pages=pages, use_layout=args.layout,
                            dpi=args.dpi, progress=lambda s: logger.info(s))
    data["sidecar"] = str(fr.write_sidecar(args.pdf, data))
    _emit(data, args.json)


def cmd_scan_math(args):
    from pdfx import scan_math

    _emit(scan_math.scan_md(args.md, args.pdf), args.json)


def cmd_extract(args):
    result = extract_pdf(
        args.pdf,
        strategy=args.strategy,
        dpi=args.dpi,
        engines=tuple(args.engines) if args.engines else DEFAULT_ENGINES,
        markers=args.markers,
        max_vision_pages=args.max_vision_pages,
    )
    text = result.pop("text", "")
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8") as f:
            f.write(text)
        result["written_to"] = args.output
    _emit(result, args.json)


def _iter_pdfs(inputs):
    configured = os.environ.get("PDFX_DERIVED_DIRS", "")
    derived = ({part.strip() for part in configured.split(",") if part.strip()}
               if configured else {"split_pdfs", "extraction", "_references"})
    for path in inputs:
        if os.path.isdir(path):
            for root, dirs, files in os.walk(path):
                dirs[:] = [d for d in dirs if d not in derived]
                for fn in sorted(files):
                    if fn.lower().endswith(".pdf"):
                        yield os.path.join(root, fn)
        elif path.lower().endswith(".pdf") and os.path.exists(path):
            yield path


def cmd_calibrate(args):
    garbles, cjk_rates, tiers = [], [], {}
    n_books = 0
    for pdf in _iter_pdfs(args.inputs):
        try:
            qs = quality_mod.scan_pdf(pdf)
        except Exception as e:
            logger.warning("skip %s: %s", pdf, e)
            continue
        n_books += 1
        logger.info("scanned %s (%d pages)", pdf, len(qs))
        for q in qs:
            if q.chars >= quality_mod.EMPTY_CHARS:
                garbles.append(q.garble)
                cjk_rates.append(q.cjk_space_rate)
            tiers[q.tier] = tiers.get(q.tier, 0) + 1

    def pct(arr, p):
        if not arr:
            return None
        arr = sorted(arr)
        k = max(0, min(len(arr) - 1, int(len(arr) * p / 100)))
        return round(arr[k], 4)

    report = {
        "books": n_books,
        "scored_pages": len(garbles),
        "tier_counts": tiers,
        "garble_percentiles": {f"p{p}": pct(garbles, p) for p in (50, 75, 90, 95, 99)},
        "cjk_space_percentiles": {f"p{p}": pct(cjk_rates, p) for p in (50, 75, 90, 95, 99)},
        "current_thresholds": {
            "garble": quality_mod.GARBLE_THRESHOLD,
            "cjk_space_rate": quality_mod.CJK_SPACE_THRESHOLD,
        },
    }
    _emit(report, args.json)


def cmd_toc_segments(args):
    segments = toc_mod.detect_page_segments(args.pdf)
    _emit({"pdf": args.pdf, "segments": segments}, args.json)


def cmd_restore_toc(args):
    _emit(toc_mod.restore_toc(args.pdf, purge=args.purge), args.json)


def cmd_inject_toc(args):
    from pdfx import inject_toc as inject_toc_mod

    try:
        report = inject_toc_mod.run(
            args.pdf,
            front_pages=args.front_pages,
            max_toc_pages=args.max_toc_pages,
            sample_check=args.sample_check,
            dpi=args.dpi,
        force_overwrite=args.force_overwrite,
        force_anchor=args.anchor_scan,
        no_llm=args.no_llm,
    )
    except inject_toc_mod.InjectTocError as e:
        sys.stderr.write(json.dumps(e.payload, ensure_ascii=False, indent=2) + "\n")
        sys.exit(e.code)
    _emit(report, as_json=True)


def cmd_check_toc(args):
    from pdfx import inject_toc as inj

    try:
        if args.apply_shift is not None:
            report = inj.apply_outline_shift(args.pdf, args.apply_shift)
        else:
            report = inj.run_check(args.pdf, samples=args.samples, dpi=args.dpi)
    except inj.InjectTocError as e:
        sys.stderr.write(json.dumps(e.payload, ensure_ascii=False, indent=2) + "\n")
        sys.exit(e.code)
    _emit(report, as_json=True)


def cmd_audit_texlayer(args):
    from pdfx import texlayer_audit as audit

    reporter = StatusReporter("audit-texlayer")

    if args.batch_archive:
        try:
            archive = Path(args.batch_archive).expanduser()
            library = Path(args.library_root).expanduser() if args.library_root else None
            books = sorted(p for p in archive.iterdir() if p.is_dir())
        except Exception as exc:  # noqa: BLE001 - emit a short terminal state
            logger.exception("batch setup failed")
            reporter.error(
                phase="validate",
                code="batch_input_invalid",
                message=_short_reason(exc),
            )
            raise SystemExit(1)

        summaries = []
        done = failed = skipped = 0
        reporter.progress("audit", 0, len(books), 0)
        for book_dir in books:
            pdfs = list(book_dir.glob("*.pdf"))
            if not pdfs:
                skipped += 1
                reporter.progress("audit", done, len(books), failed)
                continue
            name = book_dir.name
            extraction_name = os.environ.get("PDFX_EXTRACTION_DIR", "extraction")
            ext_dir = (library / name / extraction_name) if library else None
            if ext_dir is not None and not ext_dir.is_dir():
                logger.info("batch: %s has no extraction dir, skip", name)
                skipped += 1
                reporter.progress("audit", done, len(books), failed)
                continue
            logger.info("batch: auditing %s ...", name)
            try:
                rep = audit.audit_pdf(str(pdfs[0]), extraction_dir=str(ext_dir) if ext_dir else None,
                                      dpi=args.dpi, workers=args.workers,
                                      force=args.force,
                                      sampling=("off" if args.no_sampling else "auto"),
                                      sample_size=args.sample_size,
                                      deepen_full=args.full,
                                      progress=lambda s: logger.info("  %s", s),
                                      progress_event=lambda event: reporter.progress(
                                          "audit", done, len(books), failed))
                summaries.append({"book": name, "mode": rep.get("mode", "full"),
                                  "corruption_rate": rep["corruption_rate"],
                                  "counts": rep["counts"],
                                  "repair_sections": sum(1 for s in rep["sections"] if s["auto_repair"])})
                done += 1
            except Exception as e:  # noqa: BLE001 - keep batch going
                logger.exception("batch: %s failed", name)
                summaries.append({"book": name, "error": str(e)})
                failed += 1
            reporter.progress("audit", done, len(books), failed)
        _emit({"batch": summaries}, True)
        reporter.result(
            "partial" if (failed or skipped) else "ok",
            phase="complete",
            done=done,
            total=len(books),
            failed=failed,
            skipped=skipped,
        )
        return

    event_state = {"total": 0, "cache_hit": False}

    def on_event(event: dict) -> None:
        event_state.update(event)
        reporter.progress(
            "audit",
            event.get("done", 0),
            event.get("total", 0),
            event.get("failed", 0),
        )

    try:
        report = audit.audit_pdf(
            args.pdf, extraction_dir=args.extraction_dir,
            dpi=args.dpi, workers=args.workers, force=args.force,
            sampling=("off" if args.no_sampling else "auto"),
            sample_size=args.sample_size,
            deepen_full=args.full, detect_only=args.detect_only,
            progress=lambda s: logger.info("%s", s),
            progress_event=on_event,
        )
    except Exception as exc:  # noqa: BLE001 - preserve traceback before state
        logger.exception("audit-texlayer failed")
        counts = reporter.last_counts
        reporter.error(
            phase=counts.get("phase", "audit"),
            done=counts.get("done", 0),
            total=counts.get("total", 0),
            failed=max(1, counts.get("failed", 0)),
            code="audit_texlayer_failed",
            message=_short_reason(exc),
        )
        raise SystemExit(1)

    output = audit.repair_plan(report) if (args.repair_plan and not args.detect_only) else report
    _emit(output, as_json=True)

    if args.detect_only:
        reporter.result(
            "ok",
            phase="complete",
            done=0,
            total=event_state["total"],
            failed=0,
            mode="detect_only",
            gate=report.get("gate"),
        )
        return

    counts = reporter.counts_for("audit")
    reporter.result(
        "ok",
        phase="complete",
        done=report.get("audited_pages", counts["done"]),
        total=event_state["total"] or counts["total"],
        failed=0,
        cache_hit=bool(event_state.get("cache_hit", False)),
        mode=report.get("mode"),
        counts=report.get("counts", {}),
        corruption_rate=report.get("corruption_rate"),
        sampling={"gate": (report.get("sampling") or {}).get("gate")},
        arbitrated_count=report.get("arbitrated_count", 0),
        formula_region_total=report.get("formula_region_total", 0),
        soft_gate_exceeded=report.get("soft_gate_exceeded", False),
        unread_inherited_trusted=report.get("unread_inherited_trusted", 0),
        sections=len(report.get("sections") or []),
    )


def cmd_formula_audit(args):
    from pdfx import formula_audit as fa

    reporter = StatusReporter("formula-audit")
    event_state = {"total": 0, "cache_hit": False}

    def on_event(event: dict) -> None:
        event_state.update(event)
        reporter.progress(
            "audit",
            event.get("done", 0),
            event.get("total", 0),
            event.get("failed", 0),
        )

    try:
        report = fa.audit_pdf(
            args.pdf, extraction_dir=args.extraction_dir,
            dpi=args.dpi, force=args.force,
            use_layout=args.layout,
            progress=lambda s: logger.info("%s", s),
            progress_event=on_event,
        )
        if args.project and args.extraction_dir:
            fa.project_frontmatter(args.pdf, Path(args.extraction_dir))
    except Exception as exc:  # noqa: BLE001 - preserve traceback before state
        logger.exception("formula-audit failed")
        counts = reporter.last_counts
        reporter.error(
            phase=counts.get("phase", "audit"),
            done=counts.get("done", 0),
            total=counts.get("total", 0),
            failed=max(1, counts.get("failed", 0)),
            code="formula_audit_failed",
            message=_short_reason(exc),
        )
        raise SystemExit(1)

    _emit(report, as_json=True)
    summary = report.get("summary") or {}
    counts = reporter.counts_for("audit")
    total = event_state["total"] or counts["total"]
    reporter.result(
        "ok",
        phase="complete",
        done=total,
        total=total,
        failed=0,
        cache_hit=bool(event_state.get("cache_hit", False)),
        pages_audited=summary.get("pages_audited", total),
        regions=summary.get("regions", 0),
        sections=summary.get("sections", 0),
        skipped_pages=len(report.get("skipped") or {}),
        verdict_counts=summary.get("verdict_counts", {}),
        audit_verdict=_formula_audit_verdict(report),
        faudit_path=str(fa.faudit_path(str(Path(args.pdf).expanduser().resolve()))),
    )


def cmd_formula_check(args):
    """Consumer pre-check hook.

    Reads the section aggregate from the pdf's .faudit.json and returns a
    structured verdict a consumer can branch on. Does NOT repair — that is
    the formula-repair skill's job. `--target` may be a split PDF or a
    text.md path; it is resolved to its split PDF before checking.
    """
    from pdfx import formula_audit as fa

    target = str(Path(args.target).expanduser())
    pdf, extraction_dir = _resolve_consumed_target(target)
    if pdf is None:
        _emit({"verdict": "error", "reason": f"cannot resolve split PDF for {target}"}, as_json=True)
        return

    faudit = fa.load_faudit(pdf)
    if faudit is None:
        _emit({"verdict": "no_sidecar",
               "pdf": pdf,
               "reason": f".faudit.json missing for {Path(pdf).name} — run formula-audit first"},
              as_json=True)
        return

    secs = faudit.get("report", {}).get("sections") or []
    verdict = "ok"
    if len(secs) == 1:
        verdict = secs[0].get("verdict", "ok")
    elif len(secs) > 1:
        verdict = _aggregate_sections(secs)
    elif not secs:
        # Without section records, aggregate at page level so image-only PDFs
        # with unverified pages are never served as ok.
        pages = faudit.get("pages") or {}
        if pages and all(
            pv.get("page_verdict") == "ok" and pv.get("llm_ocr")
            for pv in pages.values()
        ):
            # every page was repaired by llm-ocr-refresh (`llm_ocr: true`
            # pages in the sidecar) — the scanned PDF's content has been
            # visually re-extracted; serve ok instead of unverified forever.
            verdict = "ok"
        else:
            verdict = _aggregate_sections([{"verdict": pv.get("page_verdict", "ok")}
                                           for pv in pages.values()])
        if pages and verdict == "ok":
            # a page with zero regions AND zero pages recorded is "ok" only if
            # the PDF genuinely has no formula content — every page audited.
            pass

    _emit({
        "verdict": verdict,
        "pdf": pdf,
        "extraction_dir": str(extraction_dir) if extraction_dir else None,
        "sections": secs,
    }, as_json=True)


def cmd_formula_l3_plan(args):
    """Return only the small pending-L3 plan consumed by formula-repair."""
    from pdfx import formula_audit as fa

    _emit(fa.pending_l3_plan(args.pdf, args.text_layer), as_json=True)


def cmd_formula_l3_apply(args):
    """Apply short L3 decisions written by a local runner/worker."""
    from pdfx import formula_audit as fa

    payload = json.loads(Path(args.checks_json).read_text(encoding="utf-8"))
    checks = payload.get("checks", payload) if isinstance(payload, dict) else payload
    if not isinstance(checks, list):
        raise ValueError("checks JSON must be a list or an object with checks[]")
    _emit(fa.apply_l3_checks(args.pdf, checks), as_json=True)


def _resolve_consumed_target(target: str) -> tuple[str | None, Path | None]:
    """Resolve a consumer target to (derived PDF, extraction directory)."""
    p = Path(target)
    if p.suffix.lower() == ".pdf":
        return str(p), None
    if p.name == "text.md":
        from pdfx.formula_check_cache import FormulaCacheError, canonicalize_source

        ext_dir = p.parent.parent.parent
        try:
            return str(canonicalize_source(p)), ext_dir
        except FormulaCacheError:
            pass
        return None, None
    same_pdf = p.with_suffix(".pdf")
    if same_pdf.is_file():
        return str(same_pdf), None
    return None, None


def _aggregate_sections(secs: list[dict]) -> str:
    order = {"ok": 0, "empty": 1, "pending_l3": 2, "unverified": 3, "suspect": 4}
    return max((s.get("verdict", "ok") for s in secs),
               key=lambda v: order.get(v, 0))


def main():
    ap = argparse.ArgumentParser(prog="pdfx")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("quality", help="per-page quality tiers")
    p.add_argument("pdf")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_quality)

    p = sub.add_parser("regions", help="persist formula/table region map beside the PDF (sidecar JSON)")
    p.add_argument("pdf")
    p.add_argument("--pages", default=None, help="physical pages, e.g. '3,7-9' (default all)")
    p.add_argument("--layout", action="store_true",
                   help="path B: run the configured layout provider on untrusted/empty pages")
    p.add_argument("--dpi", type=int, default=150)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_regions)

    p = sub.add_parser("scan-math", help="ambiguous plain-text math scan with region anchoring (read-only)")
    p.add_argument("md")
    p.add_argument("--pdf", default=None,
                   help="paired PDF for page attribution + region anchoring (sidecar auto-loaded)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_scan_math)

    p = sub.add_parser("extract", help="unified extraction")
    p.add_argument("pdf")
    p.add_argument("-o", "--output", help="write extracted text to this .md file")
    p.add_argument("--strategy", choices=["auto", "fast"], default="auto")
    p.add_argument("--dpi", type=int, default=200)
    p.add_argument("--engines", nargs="+", default=None,
                   help="visual engine roles (default: layout transcription native)")
    p.add_argument("--markers", action=argparse.BooleanOptionalAction, default=True,
                   help="physical page markers (default: enabled)")
    p.add_argument("--max-vision-pages", type=int, default=None, help="cap vision OCR page count (cost guard)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_extract)

    p = sub.add_parser("calibrate", help="threshold distributions over real books")
    p.add_argument("inputs", nargs="+", help="PDF files or directories")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_calibrate)

    p = sub.add_parser("toc-segments", help="segmented printed/physical offset detection")
    p.add_argument("pdf")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_toc_segments)

    p = sub.add_parser("restore-toc", help="restore outline from sidecar backup")
    p.add_argument("pdf")
    p.add_argument("--purge", action="store_true", help="delete the sidecar after restore")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_restore_toc)

    p = sub.add_parser("inject-toc", help="auto-build outline for PDFs without one")
    p.add_argument("pdf")
    p.add_argument("--front-pages", type=int, default=30)
    p.add_argument("--max-toc-pages", type=int, default=8)
    p.add_argument("--sample-check", type=int, default=5)
    p.add_argument("--dpi", type=int, default=200)
    p.add_argument("--force-overwrite", action="store_true",
                   help="replace an existing outline instead of refusing (exit 6)")
    p.add_argument("--no-llm", action="store_true",
                   help="skip the LLM TOC-understanding stage; legacy rule parsing only")
    p.add_argument("--anchor-scan", action="store_true",
                   help="skip offset mapping; locate every entry by ordered body scan "
                        "(also the automatic fallback when no printed numbers survive)")
    p.set_defaults(fn=cmd_inject_toc)

    p = sub.add_parser("check-toc", help="read-only health check of the existing outline")
    p.add_argument("pdf")
    p.add_argument("--samples", type=int, default=5,
                   help="entries sampled against page content (default 5)")
    p.add_argument("--dpi", type=int, default=200)
    p.add_argument("--apply-shift", type=int, default=None, metavar="N",
                   help="repair mode: rewrite outline with every page shifted by N "
                   "(positive = content sits deeper into the book); backs up first")
    p.add_argument("--json", action="store_true",
                   help="(report is always JSON; accepted for consistency)")
    p.set_defaults(fn=cmd_check_toc)

    p = sub.add_parser("audit-texlayer",
                       help="semantic credibility audit: dual-read text layer vs independent OCR")
    p.add_argument("pdf", nargs="?", default=None)
    p.add_argument("--extraction-dir", default=None,
                   help="extraction output dir (default: <pdf dir>/extraction)")
    p.add_argument("--dpi", type=int, default=150)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--force", action="store_true", help="ignore cached audit state")
    p.add_argument("--no-sampling", action="store_true",
                   help="skip the digital-native sample-first fast path; always full dual-read")
    p.add_argument("--sample-size", type=int, default=None, metavar="N",
                   help="sample-first page count (default 20)")
    p.add_argument("--full", action="store_true",
                   help="extend a previously sampled-finalized audit to the whole book")
    p.add_argument("--detect-only", action="store_true",
                   help="run only the zero-cost digital-native detection and print its JSON")
    p.add_argument("--repair-plan", action="store_true",
                   help="return the auto-repair decision plan instead of the full report")
    p.add_argument("--batch-archive", default=None, metavar="DIR",
                   help="audit every book archived in DIR (needs --library-root)")
    p.add_argument("--library-root", default=None, metavar="DIR",
                   help="optional artifact root containing per-item extraction directories")
    p.set_defaults(fn=cmd_audit_texlayer)

    p = sub.add_parser("formula-audit",
                       help="per-region formula correctness audit (L1 ambiguity + L2 geometry + L3 text heuristics) -> .faudit.json sidecar")
    p.add_argument("pdf")
    p.add_argument("--extraction-dir", default=None,
                   help="source artifact directory for section aggregation (default: auto-detect)")
    p.add_argument("--dpi", type=int, default=150)
    p.add_argument("--force", action="store_true", help="ignore cached faudit state")
    p.add_argument("--layout", action="store_true",
                   help="add pp-doclayout regions on untrusted/empty pages when rebuilding the regions sidecar")
    p.add_argument("--project", action="store_true",
                   help="project section verdicts into each text.md frontmatter (公式审计)")
    p.set_defaults(fn=cmd_formula_audit)

    p = sub.add_parser("formula-check",
                       help="consumer pre-check: read .faudit.json section aggregate for a split PDF or text.md (does not repair)")
    p.add_argument("target", help="split PDF path or text.md path")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_formula_check)

    p = sub.add_parser("formula-l3-plan",
                       help="plan trusted/washable pending-L3 checks without rendering pages")
    p.add_argument("pdf")
    p.add_argument("--text-layer", choices=["trusted", "washable"], default=None)
    p.set_defaults(fn=cmd_formula_l3_plan)

    p = sub.add_parser("formula-l3-apply",
                       help="persist short L3 region decisions from a JSON file")
    p.add_argument("pdf")
    p.add_argument("checks_json", help="JSON list/object containing region fingerprint decisions")
    p.set_defaults(fn=cmd_formula_l3_apply)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
