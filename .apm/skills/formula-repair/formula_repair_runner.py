#!/usr/bin/env python3
"""Deterministic state manager for resumable formula repair jobs.

This program never invokes a vision model.  A dispatcher gives a worker one
job JSON; the worker writes OCR text only under `.ocr_units/` plus a short
RESULT.json.  The runner owns the manifest and is the sole source-md writer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from argparse import Namespace
from datetime import datetime
from pathlib import Path


SCHEMA = "formula-repair-state/1"
BENCHMARK_PATH = Path(__file__).with_name("concurrency-benchmark.json")
NON_OCR_ERRORS = {"missing_page_delimiters", "missing_page_markers", "worker_registration_unavailable", "source_changed"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _monotonic_ns() -> int:
    return time.monotonic_ns()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _root_for(pdf: Path) -> Path:
    """Use an explicit source root when available, otherwise the PDF directory."""
    return pdf.resolve().parent


def _state_path(root: Path) -> Path:
    return root / ".formula-repair-state.json"


def _load_state(root: Path) -> dict:
    return _json(_state_path(root), {"schema": SCHEMA, "jobs": {}, "pdfs": {}})


def _save_state(root: Path, state: dict) -> None:
    state["schema"] = SCHEMA
    state["updated_at"] = _now()
    _write_json(_state_path(root), state)


def _job_id(pdf: Path, kind: str, members: list[dict]) -> str:
    digest = hashlib.sha256(json.dumps(members, sort_keys=True).encode()).hexdigest()[:12]
    return f"{pdf.stem}:{kind}:{digest}"


def _chunk(items: list[dict], size: int) -> list[list[dict]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def _jobs_from_faudit(pdf: Path, faudit: dict, answer_check: bool = False) -> list[dict]:
    pages, regions = [], []
    for raw_page, page in (faudit.get("pages") or {}).items():
        page_no = int(raw_page)
        # pending_l3 is NEVER an OCR job: trusted/washable layers go through the
        # lightweight L3 triage (formula-l3-plan / formula-l3-apply) instead.
        non_ok = [entry for entry in page.get("verdicts") or []
                  if entry.get("verdict") in {"suspect", "unverified"}]
        if page.get("page_verdict") in {"unverified", "suspect"} and not non_ok:
            pages.append({"page": page_no})
            continue
        for entry in non_ok:
            if entry.get("bbox_pt"):
                regions.append({"page": page_no, "bbox_pt": entry["bbox_pt"]})
            else:
                pages.append({"page": page_no})
    jobs = []
    page_size = 8 if answer_check else 12
    for members in _chunk(pages, page_size):
        jobs.append({"kind": "page", "members": members})
    for members in _chunk(regions, 20):
        jobs.append({"kind": "region", "members": members})
    return jobs


def _frontmatter_true(source: Path, key: str) -> bool:
    text = source.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return False
    end = text.find("\n---", 3)
    return end != -1 and f"{key}: true" in text[3:end]


def _aggregate_verdict(faudit: dict) -> str:
    sections = faudit.get("report", {}).get("sections") or []
    if sections:
        return "ok" if all(section.get("verdict") == "ok" for section in sections) else "non_ok"
    pages = faudit.get("pages") or {}
    return "ok" if pages and all(page.get("page_verdict") == "ok" for page in pages.values()) else "non_ok"


def _set_llm_ocr(source_bytes: bytes) -> bytes:
    text = source_bytes.decode("utf-8")
    if not text.startswith("---"):
        raise ValueError("source md has no YAML frontmatter")
    end = text.find("\n---", 3)
    if end == -1:
        raise ValueError("source md frontmatter is not closed")
    block, rest = text[:end], text[end:]
    lines = block.splitlines()
    replaced = False
    for index, line in enumerate(lines):
        if line.startswith("llm_ocr:"):
            lines[index] = "llm_ocr: true"
            replaced = True
            break
    if not replaced:
        lines.append("llm_ocr: true")
    return ("\n".join(lines) + rest).encode("utf-8")


def prepare_headings(args) -> dict:
    """Add deterministic page boundaries to a legacy source, via the runner.

    Some old textbook extracts retain standalone printed-page numbers but no
    markdown page headings.  Partial page jobs cannot safely patch such a
    source.  This structural preparation changes no extracted body text; it
    only inserts the standard headings before one verified consecutive run of
    printed-page markers.  The runner remains the sole source writer.
    """
    source = Path(args.source).resolve()
    pdf = Path(args.pdf).resolve()
    faudit = _json(pdf.with_suffix(".faudit.json"), {})
    page_count = len(faudit.get("pages") or {})
    original = source.read_text(encoding="utf-8")
    if "### 原书" in original:
        return {"status": "already_prepared", "source": str(source)}
    if page_count < 1:
        raise ValueError("no audited pages")
    lines = original.splitlines(keepends=True)
    numbers = []
    for index, line in enumerate(lines):
        value = line.strip()
        if re.fullmatch(r"\d{3,4}", value):
            numbers.append((index, int(value)))
    run = None
    for start in range(0, len(numbers) - page_count + 1):
        candidate = numbers[start:start + page_count]
        if all(candidate[i][1] + 1 == candidate[i + 1][1] for i in range(page_count - 1)):
            run = candidate
            break
    if run is None:
        raise ValueError(f"no consecutive printed-page run of length {page_count}")
    match = re.search(r"PDF物理页码:\s*[\"']?(\d+)", original)
    pdf_start = int(match.group(1)) if match else run[0][1]
    inserts = {index: f"### 原书 p.{pdf_start + offset}（印刷页 {printed}）\n\n"
               for offset, (index, printed) in enumerate(run)}
    updated = []
    for index, line in enumerate(lines):
        if index in inserts:
            updated.append(inserts[index])
        updated.append(line)
    data = "".join(updated)
    if not data.endswith("\n"):
        data += "\n"
    fd, temp = tempfile.mkstemp(prefix=f".{source.name}.headings.", dir=source.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, source)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return {"status": "prepared", "source": str(source), "pages": page_count,
            "printed_page_start": run[0][1], "headings_added": len(run)}


def normalize_headings(args) -> dict:
    """Normalize legacy inserted headings without changing extracted text."""
    source = Path(args.source).resolve()
    original = source.read_text(encoding="utf-8")
    updated = re.sub(r"(?<!\n)### 原书", "\n### 原书", original)
    if updated == original:
        return {"status": "already_normalized", "source": str(source)}
    fd, temp = tempfile.mkstemp(prefix=f".{source.name}.normalize.", dir=source.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(updated)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, source)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return {"status": "normalized", "source": str(source),
            "inserted_newlines": original.count("### 原书") - updated.count("### 原书")}


def _normalize_patch_offset(source_bytes: bytes, value: int) -> int:
    """Keep byte offsets strict, while repairing legacy character offsets.

    The worker contract is UTF-8 byte offsets.  A legacy worker may emit a
    character offset for a boundary inside a multibyte CJK character; that is
    not safe to splice directly.  Convert only an invalid UTF-8 boundary when
    the value is also a valid character index.  Valid byte boundaries remain
    untouched, and impossible values still fail closed below.
    """
    if not isinstance(value, int) or value < 0 or value > len(source_bytes):
        return value
    try:
        source_bytes[:value].decode("utf-8")
        source_bytes[value:].decode("utf-8")
        return value
    except UnicodeDecodeError:
        text = source_bytes.decode("utf-8")
        if value <= len(text):
            return len(text[:value].encode("utf-8"))
        return value


def _audit_command(pdf: Path, source: Path, textbook: bool) -> list[str]:
    """Use the established pdfx environment; no caller assembles shell text."""
    repo_cli = Path(__file__).resolve().parents[3] / "lib" / "pdfx" / "cli.py"
    if not repo_cli.is_file():
        raise ValueError(f"repo-local pdfx CLI not found: {repo_cli}")
    cli = repo_cli
    command = ["uv", "run", "--with", "pymupdf", "python3", str(cli), "formula-audit", str(pdf), "--force"]
    if textbook:
        command.extend(["--extraction-dir", str(source.parents[2]), "--project"])
    return command


def plan(args) -> dict:
    pdf = Path(args.pdf).resolve()
    root = Path(args.root).resolve() if args.root else _root_for(pdf)
    faudit_path = pdf.with_suffix(".faudit.json")
    faudit = _json(faudit_path, None)
    if faudit is None:
        raise ValueError(f"missing {faudit_path}")
    state = _load_state(root)
    source = Path(args.source).resolve()
    source_sha = _sha(source)
    pdf_key = str(pdf)
    if not args.force and _aggregate_verdict(faudit) == "ok" and _frontmatter_true(source, "llm_ocr"):
        state["pdfs"][pdf_key] = {
            "pdf": pdf_key, "source_md": str(source), "source_sha256": source_sha,
            "faudit_fingerprint": faudit.get("fingerprint"), "status": "skipped_ok",
            "completion_origin": "legacy_verified", "verified_at": _now(),
        }
        _save_state(root, state)
        return {"status": "skipped_ok", "root": str(root), "jobs_planned": 0, "state": str(_state_path(root))}
    aggregate = state["pdfs"].setdefault(pdf_key, {"pdf": pdf_key})
    # A source edit invalidates every unit result for this PDF: worker patches
    # are byte offsets against the SHA recorded at plan time, so "ok" jobs from
    # the previous plan are stale and must be redone.  A replan is not an OCR
    # failure — attempts are preserved, never consumed or reset.
    source_changed = bool(aggregate.get("source_sha256")) and aggregate["source_sha256"] != source_sha
    aggregate.update({"source_md": str(source), "source_sha256": source_sha,
                      "faudit_fingerprint": faudit.get("fingerprint"), "status": "pending"})
    aggregate.setdefault("planned_at", _now())
    aggregate.setdefault("planned_monotonic_ns", _monotonic_ns())
    planned = 0
    jobs = _jobs_from_faudit(pdf, faudit, args.answer_check)
    # Legacy textbook sources may have no per-page headings.  If the audit
    # covers every page and produced only region work, promote it to page work
    # so the worker can use the documented whole-body fallback safely.
    if not args.answer_check and "extraction" in source.parts:
        source_text = source.read_text(encoding="utf-8")
        has_page_headings = "### 原书" in source_text
        audited_pages = sorted(int(p) for p in (faudit.get("pages") or {}))
        all_pages = audited_pages == list(range(1, len(audited_pages) + 1))
        # A legacy source without page headings cannot safely accept a mixed
        # region/page plan: a page patch has no unambiguous byte range. Keep
        # the whole-body fallback only for region-only plans; longer PDFs then
        # remain bbox work and respect the 20-region limit.
        if audited_pages and len(audited_pages) <= 12 and not has_page_headings and all_pages and jobs:
            # For a short legacy section, the only safe page patch is the
            # complete section, even when the audit plan mixes bbox and page
            # units.  Longer sections keep bbox/page units as audited.
            jobs = [{"kind": "page", "members": [{"page": p} for p in audited_pages]}]
        if getattr(args, "whole_pages", False) and audited_pages:
            page_size = 8 if args.answer_check else 12
            jobs = [{"kind": "page", "members": members}
                    for members in _chunk([{"page": p} for p in audited_pages], page_size)]
    desired_ids = {_job_id(pdf, job["kind"], job["members"]) for job in jobs}
    # A prior region plan can become obsolete after the safe no-heading
    # promotion above.  Remove only non-running stale jobs for this PDF; an
    # in-flight job is never clobbered and will be handled by recover/collect.
    for stale_id, stale in list(state["jobs"].items()):
        if (stale.get("pdf") == pdf_key and stale_id not in desired_ids
                and stale.get("status") != "running"):
            del state["jobs"][stale_id]
    for job in jobs:
        jid = _job_id(pdf, job["kind"], job["members"])
        existing = state["jobs"].get(jid)
        if existing and existing.get("status") == "running":
            # In-flight unit; never clobber a lane's active work.
            continue
        if existing and existing.get("status") in {"ok", "degraded"} and not args.force:
            if existing.get("status") == "ok" and source_changed:
                if existing.get("unit_dir"):
                    shutil.rmtree(existing["unit_dir"], ignore_errors=True)
                existing.update({"status": "pending", "source_sha256": source_sha,
                                 "members": job["members"], "replanned_at": _now(),
                                 "last_error": "source_changed"})
                planned += 1
            continue
        # Preserve partial progress and retry budget across replans: a pending
        # job keeps its shrunk member list (already-OCR'd members are never
        # redone) and its attempt count.
        keep_members = (job["members"] if args.force else
                        (existing.get("members") if existing and existing.get("members") else job["members"]))
        keep_attempts = existing.get("attempts", 0) if existing else 0
        state["jobs"][jid] = {
            "job_id": jid, "pdf": pdf_key, "source_md": str(source), "source_sha256": source_sha,
            "kind": job["kind"], "members": keep_members, "status": "pending",
            "attempts": keep_attempts if not args.force else 0,
            "faudit_fingerprint": faudit.get("fingerprint"), "created_at": _now(), "last_error": "",
        }
        planned += 1
    if planned == 0 and not _all_pdf_jobs(state, pdf_key) and _aggregate_verdict(faudit) == "ok" and not args.force:
        # Nothing left to repair (e.g. pending_l3 cleared by the L3 triage);
        # register the aggregate so the manifest stays truthful for accounting.
        aggregate.update({"status": "ok", "completion_origin": "no_repair_needed", "verified_at": _now()})
    _save_state(root, state)
    return {"status": "planned", "root": str(root), "jobs_planned": planned, "state": str(_state_path(root))}


def claim(args) -> dict:
    root = Path(args.root).resolve()
    state = _load_state(root)
    candidates = [job for job in state["jobs"].values() if job.get("status") == "pending" and job.get("attempts", 0) < 3]
    job_id = getattr(args, "job_id", None)
    if job_id:
        candidates = [job for job in candidates if job.get("job_id") == job_id]
    candidates.sort(key=lambda job: (job["pdf"], job["created_at"], job["job_id"]))
    if not candidates:
        return {"status": "empty"}
    job = candidates[0]
    job["status"] = "running"
    job["attempts"] += 1
    job["started_at"] = _now()
    job["started_monotonic_ns"] = _monotonic_ns()
    job_dir = root / ".ocr_units" / hashlib.sha256(job["pdf"].encode()).hexdigest()[:16] / job["job_id"]
    job_dir.mkdir(parents=True, exist_ok=True)
    job["unit_dir"] = str(job_dir)
    _save_state(root, state)
    job_file = job_dir / "job.json"
    _write_json(job_file, job)
    return {"status": "claimed", "job_id": job["job_id"], "job_file": str(job_file)}


def _benchmark_limits(profile: str | None) -> tuple[int, int, bool]:
    data = _json(BENCHMARK_PATH, {"profiles": {}})
    item = (data.get("profiles") or {}).get(profile or "")
    if not item:
        return 1, 1, False
    workers = int(item.get("workers", 1))
    jobs_per_lane = int(item.get("jobs_per_lane", 1))
    return max(1, workers), max(1, jobs_per_lane), True


def queue(args) -> dict:
    """Write read-only lane queues without changing manifest job status.

    The dispatcher may have several reusable agent lanes, but only `claim`
    changes a job to running.  This keeps state ownership single-writer and
    lets an interrupted lane leave unclaimed work safely pending.

    Each queue entry snapshots the job's attempt count at queue time so
    `lane-next` can tell "not yet tried this round" from "tried and failed
    this round" without extra bookkeeping.
    """
    root = Path(args.root).resolve()
    state = _load_state(root)
    workers, jobs_per_lane, measured = _benchmark_limits(args.profile)
    pending = [job for job in state["jobs"].values() if job.get("status") == "pending" and job.get("attempts", 0) < 3]
    pending.sort(key=lambda job: (job["created_at"], job["pdf"], job["job_id"]))
    by_pdf: dict[str, list[dict]] = {}
    for job in pending:
        by_pdf.setdefault(job["pdf"], []).append(job)
    lanes = [[] for _ in range(workers)]
    assigned_lanes: dict[str, int] = {}
    # First give distinct PDFs a lane. A PDF never appears in two lanes, so
    # page segments sharing one source cannot be OCRed or finalized in parallel.
    for pdf in list(by_pdf):
        if len(assigned_lanes) >= workers:
            break
        assigned_lanes[pdf] = len(assigned_lanes)
        lanes[assigned_lanes[pdf]].append(by_pdf[pdf].pop(0))
        if not by_pdf[pdf]:
            del by_pdf[pdf]
    # Fill remaining capacity only in the PDF's existing lane. This still lets
    # a lane reuse one agent without violating the one-active-job-per-PDF rule.
    for pdf, lane_index in assigned_lanes.items():
        while len(lanes[lane_index]) < jobs_per_lane and by_pdf.get(pdf):
            lanes[lane_index].append(by_pdf[pdf].pop(0))
        if pdf in by_pdf and not by_pdf[pdf]:
            del by_pdf[pdf]
    queue_dir = root / ".ocr_units" / "_queues"
    queue_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for index, lane_jobs in enumerate(lanes, start=1):
        if not lane_jobs:
            continue
        entries = [{"job_id": job["job_id"], "attempts_at_queue": job.get("attempts", 0)}
                   for job in lane_jobs]
        path = queue_dir / f"lane-{index}.json"
        _write_json(path, {"schema": "formula-repair-lane-queue/2", "profile": args.profile,
                           "jobs": entries, "created_at": _now()})
        files.append(str(path))
    return {"status": "queued", "profile": args.profile, "measured": measured,
            "workers": workers, "jobs_per_lane": jobs_per_lane, "queue_files": files,
            "jobs": sum(len(lane) for lane in lanes)}


def lane_next(args) -> dict:
    """Decide (and perform) one lane's next step — the dispatcher loop core.

    Pure function of the lane queue file + manifest state; never spawns
    agents itself.  Returned actions:
      claimed       — job claimed, dispatcher runs/resumes a worker on job_file
      await_result  — job is running; dispatcher must collect (or resume the
                      same worker session) before asking again
      lane_stopped  — this lane's current job failed this round (or degraded);
                      the lane stops, later queue jobs stay pending for the
                      next fair `queue` round
      wait          — transient conflict (same PDF busy in another lane)
      done          — every job in this lane queue is ok
    """
    root = Path(args.root).resolve()
    lane_path = Path(args.lane).resolve()
    lane = _json(lane_path, None)
    if lane is None:
        return {"action": "invalid", "reason": "lane_file_missing", "lane": str(lane_path)}
    state = _load_state(root)
    for item in lane.get("jobs") or []:
        if isinstance(item, str):  # tolerate schema/1 queue files
            jid, baseline = item, 0
        else:
            jid = item.get("job_id")
            baseline = item.get("attempts_at_queue", 0)
        job = state["jobs"].get(jid)
        if job is None:
            continue
        status = job.get("status")
        if status == "ok":
            continue
        if status == "running":
            return {"action": "await_result", "job_id": jid,
                    "job_file": str(Path(job["unit_dir"]) / "job.json")}
        if status == "degraded" or job.get("attempts", 0) >= 3:
            return {"action": "lane_stopped", "job_id": jid, "reason": "job_degraded"}
        if job.get("attempts", 0) > baseline:
            return {"action": "lane_stopped", "job_id": jid, "reason": "job_failed_this_round"}
        pdf_busy = any(other.get("pdf") == job["pdf"] and other.get("status") == "running"
                       and other.get("job_id") != jid for other in state["jobs"].values())
        if pdf_busy:
            return {"action": "wait", "job_id": jid, "reason": "pdf_busy_elsewhere"}
        claimed = claim(Namespace(root=str(root), job_id=jid))
        if claimed.get("status") == "claimed":
            return {"action": "claimed", "job_id": jid, "job_file": claimed["job_file"]}
        return {"action": "wait", "job_id": jid, "reason": "claim_empty"}
    return {"action": "done"}


def recover(args) -> dict:
    root = Path(args.root).resolve()
    state = _load_state(root)
    reset, degraded = 0, 0
    for job in state["jobs"].values():
        if job.get("status") != "running":
            continue
        if job.get("attempts", 0) >= 3:
            job["status"] = "degraded"
            job["last_error"] = "interrupted after retry limit"
            degraded += 1
        else:
            job["status"] = "pending"
            job["replanned_at"] = _now()
            reset += 1
    _save_state(root, state)
    return {"status": "recovered", "pending": reset, "degraded": degraded}


def status(args) -> dict:
    """Return a context-safe aggregate; never expose OCR/unit content."""
    root = Path(args.root).resolve()
    state = _load_state(root)
    counts: dict[str, int] = {}
    for job in state.get("jobs", {}).values():
        key = job.get("status", "unknown")
        counts[key] = counts.get(key, 0) + 1
    pdfs: dict[str, int] = {}
    for item in state.get("pdfs", {}).values():
        key = item.get("status", "unknown")
        pdfs[key] = pdfs.get(key, 0) + 1
    timing = []
    for job in state.get("jobs", {}).values():
        start = job.get("started_monotonic_ns")
        end = job.get("completed_monotonic_ns")
        if isinstance(start, int) and isinstance(end, int) and end >= start:
            timing.append({"job_id": job["job_id"], "elapsed_ms": round((end - start) / 1_000_000, 3)})
    return {"schema": state.get("schema"), "job_counts": counts, "pdf_counts": pdfs,
            "job_elapsed_ms": timing, "state": str(_state_path(root))}


def _absorb_worker_counters(job: dict, result: dict) -> None:
    """Persist short numeric worker counters (never OCR prose) for benchmarks."""
    for key in ("ocr_pages", "second_reads"):
        value = result.get(key)
        if isinstance(value, int) and value >= 0:
            job[key] = job.get(key, 0) + value
    events = result.get("events") or {}
    if isinstance(events, dict):
        aggregate = job.setdefault("worker_events", {})
        for key, value in events.items():
            if isinstance(value, int) and value >= 0:
                aggregate[key] = aggregate.get(key, 0) + value


def _absorb_result_timing(job: dict, result: dict) -> None:
    """completed_monotonic_ns comes ONLY from the worker RESULT time fields.

    The runner never substitutes its own clock: a missing/garbage timestamp
    is recorded as a contract breach instead of a fabricated elapsed time.
    """
    finished = result.get("finished_monotonic_ns")
    if isinstance(finished, int):
        job["completed_monotonic_ns"] = finished
    else:
        job.pop("completed_monotonic_ns", None)
        job["timing_contract"] = "missing_finished_monotonic_ns"
    started = result.get("worker_started_monotonic_ns")
    if isinstance(started, int):
        job["worker_started_monotonic_ns"] = started


def _success_result_error(result: dict, members: list[dict]) -> str | None:
    """Require a successful worker result to account for every assigned member."""
    completed = result.get("completed_members")
    failed = result.get("failed_members", [])
    if not isinstance(completed, list) or not isinstance(failed, list):
        return "worker_incomplete"
    encode = lambda member: json.dumps(member, sort_keys=True, ensure_ascii=False)
    expected = [encode(member) for member in members]
    actual = [encode(member) for member in completed]
    if len(actual) != len(set(actual)) or set(actual) != set(expected) or failed:
        return "worker_incomplete"
    patches = result.get("patches", [])
    if not isinstance(patches, list):
        return "worker_incomplete"
    for patch in patches:
        if not isinstance(patch, dict):
            return "worker_incomplete"
        start, end, replacement = patch.get("start"), patch.get("end"), patch.get("replacement")
        if (isinstance(start, bool) or not isinstance(start, int)
                or isinstance(end, bool) or not isinstance(end, int)
                or not isinstance(replacement, str) or start < 0 or end < start):
            return "worker_incomplete"
    return None


def collect(args) -> dict:
    root = Path(args.root).resolve()
    state = _load_state(root)
    updated = 0
    for job in state["jobs"].values():
        if job.get("status") != "running" or not job.get("unit_dir"):
            continue
        result_path = Path(job["unit_dir"]) / "RESULT.json"
        result = _json(result_path, None)
        if result is None:
            continue
        if result.get("job_id") != job["job_id"]:
            continue
        if result.get("status") == "ok":
            contract_error = _success_result_error(result, job.get("members") or [])
            if contract_error:
                job["status"] = "degraded" if job.get("attempts", 0) >= 3 else "pending"
                job["last_error"] = contract_error
            else:
                job["status"] = "ok"
                job["completed_at"] = _now()
                _absorb_result_timing(job, result)
        else:
            completed = result.get("completed_members") or []
            failed = result.get("failed_members") or []
            original_members = job.get("members") or []
            valid = {json.dumps(member, sort_keys=True, ensure_ascii=False) for member in original_members}
            completed_keys = {json.dumps(member, sort_keys=True, ensure_ascii=False) for member in completed}
            failed_keys = {json.dumps(member, sort_keys=True, ensure_ascii=False) for member in failed}
            # A partial worker result may only remove members it was assigned.
            if completed_keys and completed_keys <= valid:
                source = Path(job["source_md"])
                no_page_headings = (job.get("kind") == "page" and "extraction" in source.parts
                                    and "### 原书" not in source.read_text(encoding="utf-8"))
                # A legacy source without page headings can only be patched
                # atomically after the whole page job succeeds.  Do not shrink
                # a partial result to an unpatchable subset; retry the complete
                # page segment instead.
                remaining = (original_members if no_page_headings else
                             [member for member in original_members
                              if json.dumps(member, sort_keys=True, ensure_ascii=False) not in completed_keys])
                job["members"] = remaining
                if not no_page_headings:
                    job["completed_member_count"] = job.get("completed_member_count", 0) + len(completed_keys)
                job["partial_result_at"] = _now()
                if not remaining:
                    job["status"] = "ok"
                    job["completed_at"] = _now()
                    _absorb_result_timing(job, result)
                else:
                    job["status"] = "degraded" if job.get("attempts", 0) >= 3 else "pending"
            else:
                job["status"] = "degraded" if job.get("attempts", 0) >= 3 else "pending"
            job["last_error"] = result.get("error_code", "worker_failed")
            if job["last_error"] in NON_OCR_ERRORS and job.get("attempts", 0) > 0:
                # Contract/setup failures sent no OCR request. They need a
                # replan, not a consumed vision retry.
                job["attempts"] -= 1
        _absorb_worker_counters(job, result)
        updated += 1
    _save_state(root, state)
    return {"status": "collected", "updated": updated}


def _all_pdf_jobs(state: dict, pdf: str) -> list[dict]:
    return [job for job in state["jobs"].values() if job.get("pdf") == pdf]


def finalize(args) -> dict:
    """Atomically apply worker patches only after every PDF job is complete.

    A unit worker writes byte offsets against the source SHA recorded in its
    job JSON.  The runner rejects overlap and changed sources, so no worker
    can accidentally overwrite another worker or a human edit.
    """
    root = Path(args.root).resolve()
    pdf = str(Path(args.pdf).resolve())
    state = _load_state(root)
    aggregate = state.get("pdfs", {}).get(pdf)
    if not aggregate:
        raise ValueError(f"PDF was not planned: {pdf}")
    jobs = _all_pdf_jobs(state, pdf)
    if any(job.get("status") == "degraded" for job in jobs):
        aggregate["status"] = "degraded"
        _save_state(root, state)
        return {"status": "degraded"}
    if not jobs or any(job.get("status") != "ok" for job in jobs):
        return {"status": "waiting", "remaining": sum(job.get("status") != "ok" for job in jobs)}
    source = Path(aggregate["source_md"])
    if _sha(source) != aggregate["source_sha256"]:
        aggregate["status"] = "pending"
        aggregate["replanned_at"] = _now()
        _save_state(root, state)
        return {"status": "replanned", "reason": "source_changed"}
    original = source.read_bytes()
    finalize_started_ns = _monotonic_ns()
    patches = []
    for job in jobs:
        result = _json(Path(job["unit_dir"]) / "RESULT.json", {})
        for patch in result.get("patches") or []:
            start, end = patch.get("start"), patch.get("end")
            replacement = patch.get("replacement")
            if not isinstance(start, int) or not isinstance(end, int) or not isinstance(replacement, str):
                raise ValueError(f"invalid patch in {job['job_id']}")
            start = _normalize_patch_offset(original, start)
            end = _normalize_patch_offset(original, end)
            if start < 0 or end < start or end > len(original):
                raise ValueError(f"patch outside source in {job['job_id']}")
            patches.append((start, end, replacement.encode("utf-8")))
    patches.sort(key=lambda patch: (patch[0], patch[1]))
    if any(right[0] < left[1] for left, right in zip(patches, patches[1:])):
        raise ValueError("overlapping unit patches; refusing merge")
    merged = bytearray(original)
    for start, end, replacement in reversed(patches):
        merged[start:end] = replacement
    merged = bytearray(_set_llm_ocr(bytes(merged)))
    if args.dry_run:
        return {"status": "ready", "patches": len(patches)}
    fd, temp = tempfile.mkstemp(prefix=f".{source.name}.formula-repair.", dir=source.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(merged)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, source)
        audit_command = getattr(args, "audit_command", None)
        if audit_command or getattr(args, "verify", False):
            if audit_command:
                completed = subprocess.run(shlex.split(audit_command), check=False,
                                           capture_output=True, text=True)
            else:
                textbook = "extraction" in source.parts
                completed = subprocess.run(_audit_command(Path(pdf), source, textbook), check=False,
                                           capture_output=True, text=True)
            if completed.returncode:
                source.write_bytes(original)
                aggregate["status"] = "pending"
                aggregate["last_error"] = "post_merge_audit_failed"
                aggregate["last_finalize_elapsed_ms"] = round((_monotonic_ns() - finalize_started_ns) / 1_000_000, 3)
                _save_state(root, state)
                return {"status": "rolled_back", "reason": "post_merge_audit_failed"}
        aggregate["status"] = "ok"
        aggregate["verified_at"] = _now()
        aggregate["verified_monotonic_ns"] = _monotonic_ns()
        aggregate["finalize_elapsed_ms"] = round((aggregate["verified_monotonic_ns"] - finalize_started_ns) / 1_000_000, 3)
        for job in jobs:
            shutil.rmtree(job.get("unit_dir", ""), ignore_errors=True)
        _save_state(root, state)
        return {"status": "ok", "patches": len(patches)}
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--pdf", required=True)
    p.add_argument("--source", required=True)
    p.add_argument("--root")
    p.add_argument("--answer-check", action="store_true")
    p.add_argument("--whole-pages", action="store_true",
                   help="explicitly plan audited pages sequentially (requires page boundaries for partial jobs)")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=plan)
    p = sub.add_parser("claim")
    p.add_argument("--root", required=True)
    p.add_argument("--job-id", help="claim a dispatcher-preassigned job")
    p.set_defaults(fn=claim)
    p = sub.add_parser("queue")
    p.add_argument("--root", required=True)
    p.add_argument("--profile", help="non-sensitive visual configuration fingerprint")
    p.set_defaults(fn=queue)
    p = sub.add_parser("lane-next")
    p.add_argument("--root", required=True)
    p.add_argument("--lane", required=True, help="read-only lane queue file from `queue`")
    p.set_defaults(fn=lane_next)
    p = sub.add_parser("recover")
    p.add_argument("--root", required=True)
    p.set_defaults(fn=recover)
    p = sub.add_parser("prepare-headings")
    p.add_argument("--pdf", required=True)
    p.add_argument("--source", required=True)
    p.set_defaults(fn=prepare_headings)
    p = sub.add_parser("normalize-headings")
    p.add_argument("--source", required=True)
    p.set_defaults(fn=normalize_headings)
    p = sub.add_parser("status")
    p.add_argument("--root", required=True)
    p.set_defaults(fn=status)
    p = sub.add_parser("collect")
    p.add_argument("--root", required=True)
    p.set_defaults(fn=collect)
    p = sub.add_parser("finalize")
    p.add_argument("--root", required=True)
    p.add_argument("--pdf", required=True)
    p.add_argument("--audit-command", help="runner-only post-merge audit/check command")
    p.add_argument("--verify", action="store_true", help="run the standard local formula-audit after merge")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=finalize)
    args = parser.parse_args()
    print(json.dumps(args.fn(args), ensure_ascii=False))


if __name__ == "__main__":
    main()
