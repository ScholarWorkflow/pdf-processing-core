#!/usr/bin/env python3
"""任务 R：llm-ocr-refresh 一体化批量执行脚本。

只消费上游已经确定的 repair_units.json（schema ocr-refresh-repair-units/1），
不读取、不重算、不改写 .faudit.json / quality / 审计嫌疑页 / pending_l3 /
empty+images>0 等任何上游判定；不生成、不修复、不验证图片说明。

职责：fitz 渲染、批量 glance --ocr 子进程、unit 文件落盘、state 单写、
T4 短状态（PROGRESS/RESULT/ERROR 写 stderr）、显式 finalization 原子拼回。

这不是 formula-repair（任务 F）的 unit worker 路线；两条路线并存。

用法：
  uv run --with pymupdf,pillow python3 ocr_refresh_jobs.py run \
      --target <text.md 或 split md> --pdf <split pdf> \
      --units <repair_units.json> --state <target>.ocr_repair_state.json

  uv run --with pymupdf,pillow python3 ocr_refresh_jobs.py finalize \
      --state <state> --accept <accept_plan.json>
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

UNITS_SCHEMA = "ocr-refresh-repair-units/1"
FINALIZE_SCHEMA = "ocr-refresh-finalize/1"
STATE_SCHEMA = "ocr-refresh-state/1"

READ_KINDS = ("first", "second", "third")
UNIT_KINDS = ("page", "region")
PAGE_ZOOM = 4.0
REGION_PAD_PT = 8.0
MAX_CONCURRENCY = 2
HEARTBEAT_INTERVAL_S = 30.0
UNIT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
TEXTBOOK_HEADER_RE = re.compile(r"^### 原书")
KAKOMON_MARKER_RE = re.compile(r"^<!--\s*PDF_PAGE:\s*(\d+)\s*-->\s*$")
FIGURE_LINE_RE = re.compile(r"^\s*\[图")
STRUCTURAL_LINE_RE = (
    re.compile(r"^### 原书"),
    re.compile(r"^<!--\s*PDF_PAGE:"),
)
FRONTMATTER_KEYS = ("llm_ocr", "llm_ocr_model", "llm_ocr_date", "llm_ocr_source", "llm_ocr_note")


class JobError(Exception):
    """Short, machine-readable top-level failure (no OCR content inside)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _pdfx_lib_dir() -> Path | None:
    here = Path(__file__).resolve()
    for base in list(here.parents)[:5]:
        candidate = base / "lib"
        if (candidate / "pdfx" / "status.py").is_file():
            return candidate
    return None


pdfx_lib_dir = _pdfx_lib_dir()
if pdfx_lib_dir is not None:
    sys.path.insert(0, str(pdfx_lib_dir))
from pdfx.status import StatusReporter  # noqa: E402


def default_glance_path() -> Path:
    override = os.environ.get("OCR_REFRESH_GLANCE", "").strip()
    if override:
        return Path(override).expanduser()
    executable = shutil.which("glance")
    if executable:
        return Path(executable)
    raise JobError("glance_missing", "glance is not on PATH; pass --glance or configure OCR_REFRESH_GLANCE")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def atomic_write_json(path: Path, payload: dict) -> None:
    atomic_write_bytes(
        path, (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )


def load_json_file(path: Path, code: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError as exc:
        raise JobError(code, f"cannot read {path.name}: {exc.strerror}") from exc
    except json.JSONDecodeError as exc:
        raise JobError(code, f"{path.name} is not valid JSON") from exc
    if not isinstance(data, dict):
        raise JobError(code, f"{path.name} must contain a JSON object")
    return data


class Unit:
    __slots__ = ("unit_id", "kind", "page", "bbox_pt", "read_kind", "reason",
                 "parent_unit_id", "anchor_line", "anchor_text")

    def __init__(self, unit_id, kind, page, bbox_pt, read_kind, reason,
                 parent_unit_id, anchor_line, anchor_text):
        self.unit_id = unit_id
        self.kind = kind
        self.page = page
        self.bbox_pt = bbox_pt
        self.read_kind = read_kind
        self.reason = reason
        self.parent_unit_id = parent_unit_id
        self.anchor_line = anchor_line
        self.anchor_text = anchor_text

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "page": self.page,
            "bbox_pt": list(self.bbox_pt) if self.bbox_pt is not None else None,
            "read_kind": self.read_kind,
            "reason": self.reason,
            "parent_unit_id": self.parent_unit_id,
            "anchor_line": self.anchor_line,
            "anchor_text": self.anchor_text,
        }


def parse_units(data: dict, page_count: int, page_rects) -> list[Unit]:
    if data.get("schema") != UNITS_SCHEMA:
        raise JobError("units_schema", "units file schema must be " + UNITS_SCHEMA)
    target = data.get("target")
    pdf = data.get("pdf")
    if not isinstance(target, str) or not target:
        raise JobError("bad_input", "units.target must be a non-empty path string")
    if not isinstance(pdf, str) or not pdf:
        raise JobError("bad_input", "units.pdf must be a non-empty path string")
    raw_units = data.get("units")
    if not isinstance(raw_units, list) or not raw_units:
        raise JobError("bad_input", "units.units must be a non-empty list")

    units: list[Unit] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_units):
        where = f"units[{index}]"
        if not isinstance(raw, dict):
            raise JobError("unit_invalid", where + " is not an object")
        unit_id = raw.get("unit_id")
        if not isinstance(unit_id, str) or not UNIT_ID_RE.match(unit_id):
            raise JobError("unit_invalid", where + " unit_id is missing or unsafe")
        if unit_id in seen:
            raise JobError("unit_invalid", f"{where} unit_id {unit_id} is duplicated")
        seen.add(unit_id)
        kind = raw.get("kind")
        if kind not in UNIT_KINDS:
            raise JobError("unit_invalid", f"{where} kind must be page or region")
        read_kind = raw.get("read_kind", "first")
        if read_kind not in READ_KINDS:
            raise JobError("unit_invalid", f"{where} read_kind must be one of " + ",".join(READ_KINDS))
        page = raw.get("page")
        if isinstance(page, bool) or not isinstance(page, int) or page < 1 or page > page_count:
            raise JobError("page_out_of_range", f"{where} page {page!r} outside 1..{page_count}")
        bbox = raw.get("bbox_pt")
        if kind == "page" and read_kind == "first":
            if bbox is not None:
                raise JobError("bbox_invalid", f"{where}: first page reads take no bbox_pt")
            bbox_pt = None
        else:
            bbox_pt = parse_bbox(bbox, page_rects[page - 1], where)
        parent = raw.get("parent_unit_id")
        if parent is not None and (not isinstance(parent, str) or not UNIT_ID_RE.match(parent)):
            raise JobError("unit_invalid", f"{where} parent_unit_id is unsafe")
        reason = raw.get("reason")
        if reason is not None and not isinstance(reason, str):
            raise JobError("unit_invalid", f"{where} reason must be a string")
        anchor_line = raw.get("anchor_line")
        if anchor_line is not None and (
            isinstance(anchor_line, bool) or not isinstance(anchor_line, int) or anchor_line < 1
        ):
            raise JobError("unit_invalid", f"{where} anchor_line must be a positive int")
        anchor_text = raw.get("anchor_text")
        if anchor_text is not None and not isinstance(anchor_text, str):
            raise JobError("unit_invalid", f"{where} anchor_text must be a string")
        units.append(Unit(unit_id, kind, page, bbox_pt, read_kind, reason or "",
                          parent, anchor_line, anchor_text))
    return units


def parse_bbox(raw, rect, where: str):
    if not isinstance(raw, list) or len(raw) != 4:
        raise JobError("bbox_invalid", f"{where} bbox_pt must be [x0, y0, x1, y1]")
    values = []
    for value in raw:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise JobError("bbox_invalid", f"{where} bbox_pt values must be numbers")
        if not math.isfinite(float(value)):
            raise JobError("bbox_invalid", f"{where} bbox_pt values must be finite")
        values.append(float(value))
    x0, y0, x1, y1 = values
    if x1 <= x0 or y1 <= y0:
        raise JobError("bbox_invalid", f"{where} bbox_pt must satisfy x1>x0 and y1>y0")
    tol = 0.5
    if x0 < rect.x0 - tol or y0 < rect.y0 - tol or x1 > rect.x1 + tol or y1 > rect.y1 + tol:
        raise JobError("bbox_invalid", f"{where} bbox_pt lies outside the page")
    return values


def resolve_pdf(path: Path):
    try:
        import fitz
    except ImportError as exc:
        raise JobError("pymupdf_missing", "run with: uv run --with pymupdf python3") from exc
    try:
        doc = fitz.open(str(path))
    except Exception as exc:
        raise JobError("pdf_open_failed", f"cannot open pdf: {type(exc).__name__}") from exc
    if doc.page_count < 1:
        raise JobError("pdf_open_failed", "pdf has no pages")
    return doc


def units_root_for(target: Path, pdf: Path, units_file: Path) -> Path:
    return target.parent / ".ocr_units" / pdf.stem / units_file.stem


def render_signature(unit: Unit, round_index: int, clip, pdf_sha: str) -> dict:
    return {
        "kind": unit.kind,
        "page": unit.page,
        "round": round_index,
        "clip": [round(value, 3) for value in clip] if clip is not None else None,
        "zoom": PAGE_ZOOM,
        "pad_pt": REGION_PAD_PT if unit.kind == "region" else None,
        "pdf_sha256": pdf_sha,
    }


def plan_image(unit: Unit, round_index: int, parent_png: Path | None):
    """Return (png_path_or_name, clip_mode, reuse_parent, sig_round).

    clip_mode: None = full page, "region" = bbox expanded by REGION_PAD_PT,
    "zoom" = exact bbox as given (page-level review zoom).
    """
    if unit.kind == "region":
        if unit.read_kind in ("second", "third") and parent_png is not None:
            return parent_png, "region", True, 1
        return "crop.png", "region", False, 1
    if unit.read_kind == "first":
        return "page.png", None, False, 1
    return f"zoom_r{round_index}.png", "zoom", False, round_index


def region_clip(rect, page_rect) -> list[float]:
    return [
        max(page_rect.x0, rect[0] - REGION_PAD_PT),
        max(page_rect.y0, rect[1] - REGION_PAD_PT),
        min(page_rect.x1, rect[2] + REGION_PAD_PT),
        min(page_rect.y1, rect[3] + REGION_PAD_PT),
    ]


def render_png(doc, png_path: Path, page_number: int, clip, zoom: float) -> None:
    import fitz

    page = doc.load_page(page_number - 1)
    matrix = fitz.Matrix(zoom, zoom)
    clip_rect = fitz.Rect(*clip) if clip is not None else None
    pixmap = page.get_pixmap(matrix=matrix, clip=clip_rect)
    atomic_write_bytes(png_path, pixmap.tobytes("png"))


def _round_matches_png(record: dict | None, png_path: Path, sig: dict) -> bool:
    if not record or record.get("png") != str(png_path) or record.get("render_sig") != sig:
        return False
    path = Path(record["png"])
    if not path.is_file():
        return False
    try:
        return sha256_file(path) == record.get("png_sha256")
    except OSError:
        return False


def ensure_image(doc, unit: Unit, round_index: int, unit_dir: Path, pdf_sha: str,
                 page_rects, own_round: dict | None,
                 parent_png: Path | None, parent_round: dict | None):
    """Return (png_path, png_sha256, render_sig, reused_round). Render only on mismatch.

    Region second/third reads reuse the parent's crop PNG only when the parent's
    stored render signature is identical (same clip); otherwise the unit renders
    its own crop so the parent's recorded image is never overwritten.
    """
    png_name, clip_mode, reuse_parent, sig_round = plan_image(unit, round_index, parent_png)
    page_rect = page_rects[unit.page - 1]
    if clip_mode == "region":
        clip = region_clip(unit.bbox_pt, page_rect)
    elif clip_mode == "zoom":
        clip = list(unit.bbox_pt)
    else:
        clip = None
    sig = render_signature(unit, sig_round, clip, pdf_sha)

    if reuse_parent and _round_matches_png(parent_round, parent_png, sig):
        return parent_png, sha256_file(parent_png), sig, parent_round
    png_path = parent_png if reuse_parent else unit_dir / png_name
    if reuse_parent:
        # signature mismatch with the parent: fall back to this unit's own crop
        png_path = unit_dir / "crop.png"
        own_round = None
    if _round_matches_png(own_round, png_path, sig):
        return png_path, sha256_file(png_path), sig, own_round
    render_png(doc, png_path, unit.page, clip, PAGE_ZOOM)
    return png_path, sha256_file(png_path), sig, None


def parse_call_info(stderr_text: str) -> dict | None:
    for line in reversed(stderr_text.splitlines()):
        if line.startswith("CALL_INFO "):
            try:
                info = json.loads(line[len("CALL_INFO "):])
            except json.JSONDecodeError:
                return None
            return info if isinstance(info, dict) else None
    return None


def run_glance(glance_path: Path, png_path: Path, timeout_s: float):
    """Run one glance --ocr subprocess. Returns (ocr_text, call_info, timed_out, error)."""
    env = dict(os.environ)
    env["VISION_CALL_INFO_STDERR"] = "1"
    cmd = [sys.executable, str(glance_path), str(png_path), "--ocr"]
    started = time.monotonic()
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_s, env=env, check=False
        )
    except subprocess.TimeoutExpired:
        return None, None, True, f"unit_timeout after {int(timeout_s)}s"
    except OSError as exc:
        return None, None, False, f"glance_spawn_failed: {exc.strerror}"
    elapsed_ms = int((time.monotonic() - started) * 1000)
    if proc.returncode != 0:
        detail = proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else ""
        detail = detail if len(detail) <= 200 else detail[:200]
        return None, None, False, f"glance_exit_{proc.returncode}: {detail}".strip()
    ocr_text = proc.stdout
    if not ocr_text.strip():
        return None, None, False, "empty_ocr"
    return ocr_text, parse_call_info(proc.stderr), False, ""


class Heartbeat:
    def __init__(self, reporter: StatusReporter, phase_getter):
        self._reporter = reporter
        self._phase_getter = phase_getter
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def _loop(self):
        while not self._stop.wait(HEARTBEAT_INTERVAL_S):
            counts = self._phase_getter()
            print(
                f"[heartbeat] phase={counts.get('phase', 'ocr')} done={counts.get('done', 0)} "
                f"total={counts.get('total', 0)} elapsed_s={self._reporter.elapsed_s()}",
                file=sys.stderr,
                flush=True,
            )

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_exc):
        self._stop.set()
        self._thread.join(timeout=5)
        return False


def load_state(path: Path) -> dict | None:
    if not path.is_file():
        return None
    state = load_json_file(path, "state_invalid")
    if state.get("schema") != STATE_SCHEMA:
        raise JobError("state_invalid", "state schema must be " + STATE_SCHEMA)
    return state


def fresh_state(target: Path, pdf: Path, pdf_sha: str, source_sha: str) -> dict:
    return {
        "schema": STATE_SCHEMA,
        "target": str(target),
        "pdf": str(pdf),
        "pdf_sha256": pdf_sha,
        "source_sha256": source_sha,
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "updated_at": "",
        "reads": {"first": 0, "second": 0, "third": 0},
        "units": {},
    }


def attempt_is_reusable(attempt: dict) -> bool:
    if attempt.get("status") != "ok":
        return False
    png = attempt.get("png")
    ocr = attempt.get("ocr")
    if not png or not ocr:
        return False
    if not Path(png).is_file() or not Path(ocr).is_file():
        return False
    try:
        if sha256_file(Path(png)) != attempt.get("png_sha256"):
            return False
        if sha256_file(Path(ocr)) != attempt.get("ocr_sha256"):
            return False
    except OSError:
        return False
    return True


def ocr_has_structural_lines(text: str) -> bool:
    return any(pattern.match(line) for line in text.splitlines() for pattern in STRUCTURAL_LINE_RE)


def cmd_run(args: argparse.Namespace) -> int:
    reporter = StatusReporter("ocr_refresh_jobs")
    target = Path(args.target).expanduser()
    pdf_path = Path(args.pdf).expanduser()
    units_file = Path(args.units).expanduser()
    state_path = Path(args.state).expanduser()
    if not target.is_file():
        raise JobError("bad_input", f"target not found: {target}")
    if not pdf_path.is_file():
        raise JobError("bad_input", f"pdf not found: {pdf_path}")
    if args.concurrency < 1 or args.concurrency > MAX_CONCURRENCY:
        raise JobError(
            "concurrency_unvalidated",
            f"concurrency must be 1..{MAX_CONCURRENCY}; values above {MAX_CONCURRENCY} "
            "need a benchmark validated with F's method first",
        )
    glance_path = Path(args.glance).expanduser() if args.glance else default_glance_path()
    if not glance_path.is_file():
        raise JobError("bad_input", f"glance not found: {glance_path}")

    data = load_json_file(units_file, "units_invalid")
    doc = resolve_pdf(pdf_path)
    try:
        page_rects = [doc.load_page(i).rect for i in range(doc.page_count)]
        units = parse_units(data, doc.page_count, page_rects)
        pdf_sha = sha256_file(pdf_path)
        source_sha = sha256_file(target)
        state = load_state(state_path)
        if state is not None and (state.get("target") != str(target) or state.get("pdf") != str(pdf_path)):
            raise JobError("state_invalid", "state belongs to a different target/pdf")
        if state is None:
            state = fresh_state(target, pdf_path, pdf_sha, source_sha)
        root = units_root_for(target, pdf_path, units_file)

        for unit in units:
            entry = state["units"].setdefault(unit.unit_id, unit.as_dict())
            entry.setdefault("status", "pending")
            entry.setdefault("attempts", [])
            if entry.get("status") == "ok" and not entry.get("attempts"):
                entry["status"] = "pending"
        parent_ids = {unit.parent_unit_id for unit in units if unit.parent_unit_id}
        unknown_parents = parent_ids - set(state["units"])
        if unknown_parents:
            raise JobError("unit_invalid", "unknown parent_unit_id: " + ",".join(sorted(unknown_parents)))
        atomic_write_json(state_path, state)

        pending: list[Unit] = []
        for unit in units:
            entry = state["units"][unit.unit_id]
            if entry["attempts"] and attempt_is_reusable(entry["attempts"][-1]):
                entry["status"] = "ok"
                continue
            pending.append(unit)

        total = len(pending)
        done = failed = 0
        phase_counts = {"phase": "render", "done": 0, "total": total}
        timeout_s = float(os.environ.get("OCR_REFRESH_UNIT_TIMEOUT_S", "1800"))

        # Phase A: render serially in the parent thread (fitz is not thread-safe).
        images: dict[str, dict] = {}
        for index, unit in enumerate(pending, start=1):
            entry = state["units"][unit.unit_id]
            attempts = entry["attempts"]
            round_index = len(attempts) + 1
            parent_png = None
            parent_round = None
            if unit.parent_unit_id:
                parent_attempts = state["units"].get(unit.parent_unit_id, {}).get("attempts") or []
                if parent_attempts and parent_attempts[-1].get("status") == "ok":
                    parent_png = Path(parent_attempts[-1]["png"])
                    parent_round = parent_attempts[-1]
            started = time.monotonic()
            png_path, png_sha, sig, reused = ensure_image(
                doc, unit, round_index, root / unit.unit_id, pdf_sha, page_rects,
                attempts[-1] if attempts else None, parent_png, parent_round,
            )
            images[unit.unit_id] = {
                "round_index": round_index,
                "png_path": png_path,
                "png_sha": png_sha,
                "sig": sig,
                "reused": bool(reused),
                "started_monotonic": started,
            }
            phase_counts["done"] = index
            reporter.progress("render", index, total, 0)

        def read_unit(unit: Unit):
            """Worker: one glance subprocess. Touches no shared state, writes no files."""
            image = images[unit.unit_id]
            ocr_text, call_info, timed_out, error = run_glance(
                glance_path, image["png_path"], timeout_s
            )
            finished = time.monotonic()
            return unit, {
                "ocr_text": ocr_text,
                "call_info": call_info,
                "error": ("unit_timeout" if timed_out else error),
                "finished_monotonic": finished,
                "started_monotonic": image["started_monotonic"],
            }

        phase_counts = {"phase": "ocr", "done": 0, "total": total}
        with Heartbeat(reporter, lambda: phase_counts):
            if pending:
                reporter.progress("ocr", 0, total, 0)
            with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
                futures = [pool.submit(read_unit, unit) for unit in pending]
                for future in concurrent.futures.as_completed(futures):
                    unit, outcome = future.result()
                    image = images[unit.unit_id]
                    entry = state["units"][unit.unit_id]
                    attempt = {
                        "read_kind": unit.read_kind,
                        "round": image["round_index"],
                        "png": str(image["png_path"]),
                        "png_sha256": image["png_sha"],
                        "render_sig": image["sig"],
                        "ocr": "",
                        "ocr_sha256": "",
                        "model": "",
                        "chain_name": "",
                        "tier": "",
                        "started_monotonic": outcome["started_monotonic"],
                        "finished_monotonic": outcome["finished_monotonic"],
                        "elapsed_ms": int(
                            (outcome["finished_monotonic"] - outcome["started_monotonic"]) * 1000
                        ),
                        "status": "failed",
                        "error": "",
                        "reused_image": image["reused"],
                    }
                    if outcome["error"]:
                        attempt["error"] = outcome["error"]
                    else:
                        ocr_path = root / unit.unit_id / f"attempt{image['round_index']}.ocr.md"
                        atomic_write_bytes(ocr_path, outcome["ocr_text"].encode("utf-8"))
                        attempt["ocr"] = str(ocr_path)
                        attempt["ocr_sha256"] = sha256_file(ocr_path)
                        call_info = outcome["call_info"]
                        if call_info:
                            attempt["model"] = str(call_info.get("model", ""))
                            attempt["chain_name"] = str(call_info.get("name", ""))
                            attempt["tier"] = str(call_info.get("tier", ""))
                        if ocr_has_structural_lines(outcome["ocr_text"]):
                            attempt["error"] = "structural_lines_in_ocr"
                        else:
                            attempt["status"] = "ok"
                    if len(entry["attempts"]) + 1 == attempt["round"]:
                        entry["attempts"].append(attempt)
                    else:
                        entry["attempts"] = entry["attempts"][: attempt["round"] - 1]
                        entry["attempts"].append(attempt)
                    if attempt["status"] == "ok":
                        entry["status"] = "ok"
                        entry["error"] = ""
                        done += 1
                        state["reads"][unit.read_kind] = state["reads"].get(unit.read_kind, 0) + 1
                    else:
                        entry["status"] = "failed"
                        entry["error"] = attempt["error"]
                        failed += 1
                    phase_counts["done"] = done + failed
                    reporter.progress("ocr", done + failed, total, failed)
                    atomic_write_json(state_path, state)

        state["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        state["source_sha256"] = sha256_file(target)
        atomic_write_json(state_path, state)
        status = "ok" if failed == 0 else "partial"
        reporter.result(
            status,
            phase="complete",
            done=done + failed,
            total=total,
            failed=failed,
            units_ok=done,
            units_failed=failed,
            reads=dict(state["reads"]),
            state_path=str(state_path),
        )
        return 0
    finally:
        try:
            doc.close()
        except Exception:
            pass


def split_source_lines(source_text: str):
    """Return (frontmatter_lines, body_lines). frontmatter includes the --- fences."""
    lines = source_text.splitlines()
    if lines and lines[0].strip() == "---":
        for index in range(1, len(lines)):
            if lines[index].strip() == "---":
                return lines[: index + 1], lines[index + 1:]
    return [], lines


def page_sections(body_lines: list[str]):
    """Return list of (kind, key, header_line_index). kind in {textbook, kakomon}."""
    is_kakomon = any(KAKOMON_MARKER_RE.match(line) for line in body_lines)
    sections = []
    for index, line in enumerate(body_lines):
        if is_kakomon:
            match = KAKOMON_MARKER_RE.match(line)
            if match:
                sections.append(("kakomon", int(match.group(1)), index))
        elif TEXTBOOK_HEADER_RE.match(line):
            sections.append(("textbook", len(sections) + 1, index))
    return ("kakomon" if is_kakomon else "textbook"), sections


def section_range(sections, position: int, body_length: int):
    """Body line range [start, end) covered by the section at sections[position]."""
    header_index = sections[position][2]
    start = header_index + 1
    end = sections[position + 1][2] if position + 1 < len(sections) else body_length
    return start, end


def update_frontmatter(frontmatter_lines: list[str], model: str, note: str) -> list[str]:
    today = datetime.date.today().isoformat()
    values = {
        "llm_ocr": "true",
        "llm_ocr_model": json.dumps(model, ensure_ascii=False),
        "llm_ocr_date": json.dumps(today, ensure_ascii=False),
        "llm_ocr_source": json.dumps("llm-ocr-refresh 一体化批量执行（R）", ensure_ascii=False),
        "llm_ocr_note": json.dumps(note, ensure_ascii=False),
    }
    lines = list(frontmatter_lines)
    if not lines:
        lines = ["---", "---"]
    for key, value in values.items():
        pattern = re.compile(r"^(" + re.escape(key) + r"\s*:\s*)(.*)$")
        replaced = False
        for index, line in enumerate(lines[1:-1], start=1):
            if pattern.match(line):
                lines[index] = key + ": " + value
                replaced = True
                break
        if not replaced:
            lines.insert(len(lines) - 1, key + ": " + value)
    return lines


def cmd_finalize(args: argparse.Namespace) -> int:
    reporter = StatusReporter("ocr_refresh_jobs")
    state_path = Path(args.state).expanduser()
    accept_path = Path(args.accept).expanduser()
    state = load_state(state_path)
    if state is None:
        raise JobError("state_missing", "run the batch first; state file not found")
    target = Path(state["target"])
    pdf_path = Path(state["pdf"])
    if not target.is_file():
        raise JobError("bad_input", f"target not found: {target}")
    plan = load_json_file(accept_path, "accept_invalid")
    if plan.get("schema") != FINALIZE_SCHEMA:
        raise JobError("accept_invalid", "accept plan schema must be " + FINALIZE_SCHEMA)
    if plan.get("target") not in (None, str(target)) or plan.get("pdf") not in (None, str(pdf_path)):
        raise JobError("accept_invalid", "accept plan target/pdf does not match state")

    current_source_sha = sha256_file(target)
    if current_source_sha != state.get("source_sha256"):
        raise JobError(
            "source_changed",
            "source changed after the last run; re-run the batch to refresh anchors",
        )
    source_text = target.read_text(encoding="utf-8")

    accept_ids = plan.get("accept")
    resolved_ids = plan.get("resolved", [])
    if not isinstance(accept_ids, list) or not all(isinstance(i, str) for i in accept_ids):
        raise JobError("accept_invalid", "accept must be a list of unit_id strings")
    if not isinstance(resolved_ids, list) or not all(isinstance(i, str) for i in resolved_ids):
        raise JobError("accept_invalid", "resolved must be a list of unit_id strings")
    accept_set, resolved_set = set(accept_ids), set(resolved_ids)
    if accept_set & resolved_set:
        raise JobError("accept_invalid", "a unit_id cannot be both accepted and resolved")
    unknown = (accept_set | resolved_set) - set(state["units"])
    if unknown:
        raise JobError("accept_invalid", "unknown unit_id: " + ",".join(sorted(unknown))[:200])
    unresolved = set(state["units"]) - accept_set - resolved_set
    if unresolved:
        raise JobError(
            "unresolved_units",
            f"{len(unresolved)} unit(s) neither accepted nor resolved: "
            + ",".join(sorted(unresolved)[:10]),
        )

    failed_accepted = [
        unit_id for unit_id in accept_set
        if state["units"][unit_id].get("status") != "ok"
        or not state["units"][unit_id].get("attempts")
        or state["units"][unit_id]["attempts"][-1].get("status") != "ok"
    ]
    if failed_accepted:
        raise JobError(
            "accept_failed_unit",
            "accepted units without a legal ok result: " + ",".join(sorted(failed_accepted))[:10],
        )
    failed_resolved = [unit_id for unit_id in resolved_set if state["units"][unit_id].get("status") == "pending"]
    if failed_resolved:
        raise JobError(
            "unresolved_units",
            "resolved units still pending (batch not finished): " + ",".join(sorted(failed_resolved))[:10],
        )
    for unit_id in accept_set:
        entry = state["units"][unit_id]
        if not attempt_is_reusable(entry["attempts"][-1]):
            raise JobError("accept_failed_unit", f"unit {unit_id} result files failed verification")

    frontmatter, body = split_source_lines(source_text)
    mode, sections = page_sections(body)
    if not sections:
        raise JobError("missing_page_delimiters", "no page delimiters found in source")
    lookup = {key: position for position, (_kind, key, _index) in enumerate(sections)}
    if mode == "textbook":
        expected = list(range(1, len(sections) + 1))
        if sorted(lookup) != expected:
            raise JobError("missing_page_delimiters", "textbook page headers are not contiguous")

    page_patches: dict[int, dict] = {}
    line_patches: dict[int, dict] = {}
    for unit_id in sorted(accept_set):
        entry = state["units"][unit_id]
        if entry["kind"] != "page" and entry["kind"] != "region":
            raise JobError("accept_invalid", f"unit {unit_id} has unknown kind")
        page = entry["page"]
        if page not in lookup:
            raise JobError("page_out_of_source", f"unit {unit_id} page {page} has no delimiter in source")
        position = lookup[page]
        start, end = section_range(sections, position, len(body))
        ocr_text = Path(entry["attempts"][-1]["ocr"]).read_text(encoding="utf-8")
        if entry["kind"] == "page":
            if page in page_patches:
                raise JobError("patch_overlap", f"two page units target page {page}")
            page_patches[page] = {"position": position, "start": start, "end": end, "text": ocr_text, "unit_id": unit_id}
        else:
            anchor_line = entry.get("anchor_line")
            anchor_text = entry.get("anchor_text")
            target_line = None
            if anchor_line is not None:
                if isinstance(anchor_line, bool) or not isinstance(anchor_line, int):
                    raise JobError("region_unlocatable", f"unit {unit_id} anchor_line must be an int")
                if anchor_line < 1 or anchor_line > len(body):
                    raise JobError("region_unlocatable", f"unit {unit_id} anchor_line outside source body")
                target_line = anchor_line - 1
                if not (start <= target_line < end):
                    raise JobError("region_unlocatable", f"unit {unit_id} anchor_line outside its page section")
            elif isinstance(anchor_text, str) and anchor_text.strip():
                matches = [
                    index for index in range(start, end)
                    if body[index].strip() == anchor_text.strip()
                ]
                if len(matches) != 1:
                    raise JobError(
                        "region_unlocatable",
                        f"unit {unit_id} anchor_text matched {len(matches)} lines; "
                        "submit a page-level upgrade plan instead",
                    )
                target_line = matches[0]
            else:
                raise JobError(
                    "region_unlocatable",
                    f"unit {unit_id} has no anchor_line/anchor_text; "
                    "submit a page-level upgrade plan instead",
                )
            if target_line in line_patches:
                raise JobError("patch_overlap", f"two region units target the same line (unit {unit_id})")
            line_patches[target_line] = {"text": ocr_text, "unit_id": unit_id, "start": start, "end": end}

    for line_index, patch in line_patches.items():
        for page_patch in page_patches.values():
            if page_patch["start"] <= line_index < page_patch["end"]:
                raise JobError(
                    "patch_overlap",
                    f"region unit {patch['unit_id']} overlaps accepted page unit {page_patch['unit_id']}",
                )

    # Build a single, non-overlapping patch list and splice once: page patches
    # replace their whole section range, region patches replace exactly one
    # original line. Indices all refer to the ORIGINAL body, so ordering the
    # splice by start offset avoids line-drift between the two patch kinds.
    patches = []
    for page, patch in page_patches.items():
        patches.append((patch["start"], patch["end"], patch["text"], patch["unit_id"]))
    for line_index, patch in line_patches.items():
        patches.append((line_index, line_index + 1, patch["text"], patch["unit_id"]))
    patches.sort(key=lambda item: item[0])
    for (start_a, end_a, _, _), (start_b, _end_b, _, _) in zip(patches, patches[1:]):
        if start_b < end_a:
            raise JobError("patch_overlap", f"patch ranges overlap at body line {start_b}")

    new_body: list[str] = []
    cursor = 0
    for start, end, text, _unit_id in patches:
        new_body.extend(body[cursor:start])
        old_section = body[start:end]
        if end - start > 1:  # page-level replacement: carry existing figure lines
            carried = [line for line in old_section if FIGURE_LINE_RE.match(line)]
            new_body.extend(text.rstrip("\n").splitlines() + carried)
        else:
            new_body.extend(text.rstrip("\n").splitlines())
        cursor = end
    new_body.extend(body[cursor:])

    models = []
    notes = []
    question_marks = 0
    for unit_id in sorted(accept_set):
        entry = state["units"][unit_id]
        attempt = entry["attempts"][-1]
        model = attempt.get("model") or attempt.get("chain_name") or "unknown"
        if model not in models:
            models.append(model)
        ocr_text = Path(attempt["ocr"]).read_text(encoding="utf-8")
        question_marks += ocr_text.count("[?]")
        if entry["kind"] == "region":
            notes.append(f"{unit_id}: region 行级替换")
    note = "一体化批量执行（R）；不确定处已标 [?]"
    if question_marks:
        note += f"（共 {question_marks} 处）"
    if notes:
        note += "；region：" + ",".join(notes[:5]) + ("…" if len(notes) > 5 else "")

    new_frontmatter = update_frontmatter(frontmatter, ",".join(models), note)
    new_text = "\n".join(new_frontmatter + new_body)
    if source_text.endswith("\n") and not new_text.endswith("\n"):
        new_text += "\n"

    atomic_write_bytes(target, new_text.encode("utf-8"))
    state["finalized_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    state["finalized_source_sha256"] = sha256_file(target)
    atomic_write_json(state_path, state)
    reporter.result(
        "ok",
        phase="finalize",
        done=len(accept_set),
        total=len(state["units"]),
        failed=0,
        pages_repaired=sorted(page_patches),
        regions_repaired=len(line_patches),
        resolved=len(resolved_set),
        source_path=str(target),
        state_path=str(state_path),
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ocr_refresh_jobs", description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="execute a batch of repair units")
    run_parser.add_argument("--target", required=True)
    run_parser.add_argument("--pdf", required=True)
    run_parser.add_argument("--units", required=True)
    run_parser.add_argument("--state", required=True)
    run_parser.add_argument("--concurrency", type=int, default=MAX_CONCURRENCY)
    run_parser.add_argument("--glance", default=None,
                            help="OCR executable; defaults to glance found on PATH")
    run_parser.set_defaults(func=cmd_run)

    finalize_parser = subparsers.add_parser("finalize", help="apply accepted results to source")
    finalize_parser.add_argument("--state", required=True)
    finalize_parser.add_argument("--accept", required=True)
    finalize_parser.set_defaults(func=cmd_finalize)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    reporter = StatusReporter("ocr_refresh_jobs")
    try:
        return args.func(args)
    except JobError as exc:
        reporter.error(
            phase=args.command, code=exc.code, message=exc.message[:200]
        )
        return 1
    except Exception as exc:
        reporter.error(
            phase=args.command, code="internal_error",
            message=f"{type(exc).__name__}: {exc}"[:200],
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
