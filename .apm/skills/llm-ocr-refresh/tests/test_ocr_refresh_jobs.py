#!/usr/bin/env python3
"""No-model fixture tests for the R batch script (ocr_refresh_jobs.py).

Every test uses a runtime-generated mini PDF, a copied fixture source md and
a fake glance executable. No real vision service, real textbook, real
kakomon or real knowledge base is touched.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import fitz

SKILL_DIR = Path(__file__).resolve().parents[1]
R_PATH = SKILL_DIR / "ocr_refresh_jobs.py"
SPEC = importlib.util.spec_from_file_location("ocr_refresh_jobs", R_PATH)
R = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(R)

READ_STATUS = Path(__file__).resolve().parents[4] / "lib" / "pdfx" / "read_status.py"

FAKE_GLANCE = r'''
import json, os, sys, time
from pathlib import Path

argv = sys.argv[1:]
png = argv[0] if argv else ""
probe_dir = Path(os.environ["OCR_REFRESH_TEST_PROBE"])
probe_dir.mkdir(parents=True, exist_ok=True)
with open(probe_dir / "calls.jsonl", "a") as handle:
    handle.write(json.dumps({"png": png, "t0": time.time()}) + "\n")

if "--ocr" not in argv:
    print("fake glance only supports --ocr", file=sys.stderr)
    sys.exit(3)

sleep_s = float(os.environ.get("OCR_REFRESH_TEST_SLEEP", "0"))
if sleep_s:
    time.sleep(sleep_s)

name = Path(png).name + "@" + Path(png).parent.name
if "fail" in png:
    print("glance: simulated failure", file=sys.stderr)
    sys.exit(1)
if "flaky" in png:
    calls = sum(1 for line in open(probe_dir / "calls.jsonl") if png in line)
    if calls <= 1:
        print("glance: flaky first attempt", file=sys.stderr)
        sys.exit(1)
if "empty" in png:
    sys.exit(0)
if "struct" in png:
    print("intro\n<!-- PDF_PAGE: 3 -->\noutro")
else:
    text = "OCR[" + name + "] 設問 $x^2+y^2=1$"
    if "doubt" in png:
        text += " 不確 [?]"
    print(text)
    print("second line of " + name)

print("CALL_INFO " + json.dumps({"name": "fake-chain", "model": "fake-model", "tier": "free"}),
      file=sys.stderr)
with open(probe_dir / "calls.jsonl", "a") as handle:
    handle.write(json.dumps({"png": png, "t1": time.time()}) + "\n")
'''


def probe_calls(probe_dir: Path) -> list[dict]:
    path = probe_dir / "calls.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def call_count(probe_dir: Path, needle: str) -> int:
    return sum(1 for record in probe_calls(probe_dir) if needle in record["png"] and "t1" in record)


def parse_lines(stderr_text: str, kind: str) -> list[dict]:
    payloads = []
    for line in stderr_text.splitlines():
        if line.startswith(kind + " "):
            payloads.append(json.loads(line[len(kind) + 1:]))
    return payloads


class RFixture(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name) / "book"
        self.root.mkdir()
        self.probe = self.root / "probe"
        self.pdf = self.root / "section.pdf"
        doc = fitz.open()
        for number in (1, 2):
            page = doc.new_page(width=595, height=842)
            page.insert_text((72, 100), f"fixture page {number}")
        doc.save(str(self.pdf))
        doc.close()
        self.glance = self.root / "fake_glance.py"
        self.glance.write_text(FAKE_GLANCE)
        self._saved_probe_env = os.environ.get("OCR_REFRESH_TEST_PROBE")

    def tearDown(self):
        if self._saved_probe_env is None:
            os.environ.pop("OCR_REFRESH_TEST_PROBE", None)
        else:
            os.environ["OCR_REFRESH_TEST_PROBE"] = self._saved_probe_env
        self._temp.cleanup()

    # ── fixture builders ────────────────────────────────────────────────

    def textbook_source(self) -> Path:
        source = self.root / "text.md"
        source.write_text(
            "---\n"
            "title: fixture\n"
            "原书页码: [11, 12]\n"
            "llm_ocr: false\n"
            "---\n"
            "### 原书 p.11（印刷页 1）\n"
            "旧乱码 ααα ~bad~\n"
            "[图 p.11-1: fixture 旧图描述，必须保留]\n"
            "\n"
            "### 原书 p.12（印刷页 2）\n"
            "第二页原文 keep-me-line\n",
            encoding="utf-8",
        )
        return source

    def kakomon_source(self) -> Path:
        source = self.root / "2022_数学.md"
        source.write_text(
            "---\n"
            "school: fixture\n"
            "---\n"
            "<!-- PDF_PAGE: 1 -->\n"
            "旧乱码 βββ\n"
            "\n"
            "<!-- PDF_PAGE: 2 -->\n"
            "第二页原题 keep-me-line\n",
            encoding="utf-8",
        )
        return source

    def units_file(self, name: str, units: list[dict], target: Path | None = None) -> Path:
        target = target or self.root / "text.md"
        path = self.root / name
        path.write_text(json.dumps({
            "schema": R.UNITS_SCHEMA,
            "target": str(target),
            "pdf": str(self.pdf),
            "units": units,
        }, ensure_ascii=False), encoding="utf-8")
        return path

    def accept_file(self, name: str, accept: list[str], resolved: list[str],
                    target: Path) -> Path:
        path = self.root / name
        path.write_text(json.dumps({
            "schema": R.FINALIZE_SCHEMA,
            "target": str(target),
            "pdf": str(self.pdf),
            "accept": accept,
            "resolved": resolved,
        }, ensure_ascii=False), encoding="utf-8")
        return path

    def run_r(self, argv: list[str]) -> tuple[int, str]:
        os.environ["OCR_REFRESH_TEST_PROBE"] = str(self.probe)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = R.main(argv)
        return code, stderr.getvalue()

    def run_batch(self, target: Path, units: Path, concurrency: int = 2) -> tuple[int, str]:
        return self.run_r([
            "run",
            "--target", str(target),
            "--pdf", str(self.pdf),
            "--units", str(units),
            "--state", str(target) + ".ocr_repair_state.json",
            "--concurrency", str(concurrency),
            "--glance", str(self.glance),
        ])

    def state_of(self, target: Path) -> dict:
        return json.loads(Path(str(target) + ".ocr_repair_state.json").read_text())

    # ── tests ───────────────────────────────────────────────────────────

    def test_units_consumed_not_recomputed(self):
        source = self.textbook_source()
        units = self.units_file("u1.json", [
            {"unit_id": "p1", "kind": "page", "page": 1, "read_kind": "first", "reason": "fixture"},
        ])
        code, err = self.run_batch(source, units)
        self.assertEqual(code, 0, err)
        # exactly the listed unit was executed; no .faudit.json exists at all
        self.assertEqual(call_count(self.probe, "p1/page.png"), 1)
        self.assertEqual(len(probe_calls(self.probe)), 2)  # start+end of one call
        self.assertFalse(list(self.root.rglob("*.faudit.json")))
        result = parse_lines(err, "RESULT")[-1]
        self.assertEqual(result["status"], "ok")

    def test_invalid_input_errors_before_any_vision_call(self):
        source = self.textbook_source()
        units = self.units_file("bad.json", [
            {"unit_id": "p9", "kind": "page", "page": 99, "read_kind": "first"},
        ])
        code, err = self.run_batch(source, units)
        self.assertEqual(code, 1)
        errors = parse_lines(err, "ERROR")
        self.assertEqual(errors[-1]["code"], "page_out_of_range")
        self.assertEqual(probe_calls(self.probe), [])

    def test_concurrency_above_two_is_rejected(self):
        source = self.textbook_source()
        units = self.units_file("u1.json", [
            {"unit_id": "p1", "kind": "page", "page": 1, "read_kind": "first"},
        ])
        code, err = self.run_batch(source, units, concurrency=3)
        self.assertEqual(code, 1)
        self.assertEqual(parse_lines(err, "ERROR")[-1]["code"], "concurrency_unvalidated")
        self.assertEqual(probe_calls(self.probe), [])

    def test_page_render_matrix4_and_region_pad(self):
        source = self.textbook_source()
        units = self.units_file("u1.json", [
            {"unit_id": "p1", "kind": "page", "page": 1, "read_kind": "first"},
            {"unit_id": "r1", "kind": "region", "page": 2, "read_kind": "first",
             "bbox_pt": [100, 100, 200, 140]},
        ])
        code, err = self.run_batch(source, units)
        self.assertEqual(code, 0, err)
        root = self.root / ".ocr_units" / "section" / "u1"
        page_png = fitz.Pixmap(str(root / "p1" / "page.png"))
        self.assertAlmostEqual(page_png.width, 595 * 4, delta=4)
        self.assertAlmostEqual(page_png.height, 842 * 4, delta=4)
        crop_png = fitz.Pixmap(str(root / "r1" / "crop.png"))
        self.assertAlmostEqual(crop_png.width, (116 * 4), delta=4)   # (100-8)..(200+8)
        self.assertAlmostEqual(crop_png.height, (56 * 4), delta=4)   # (100-8)..(140+8)

    def test_review_plans_batch_second_and_third_reads(self):
        source = self.textbook_source()
        first = self.units_file("first.json", [
            {"unit_id": "p1", "kind": "page", "page": 1, "read_kind": "first"},
            {"unit_id": "r1", "kind": "region", "page": 1, "read_kind": "first",
             "bbox_pt": [100, 100, 300, 160]},
        ])
        code, _ = self.run_batch(source, first)
        self.assertEqual(code, 0)
        second = self.units_file("second.json", [
            {"unit_id": "p1_zoom", "kind": "page", "page": 1, "read_kind": "second",
             "bbox_pt": [72, 80, 360, 200], "parent_unit_id": "p1"},
        ])
        code, _ = self.run_batch(source, second)
        self.assertEqual(code, 0)
        state = self.state_of(source)
        self.assertEqual(state["reads"], {"first": 2, "second": 1, "third": 0})
        zoom_png = Path(state["units"]["p1_zoom"]["attempts"][-1]["png"])
        self.assertIn("zoom_r1.png", zoom_png.name)
        full_png = fitz.Pixmap(str(self.root / ".ocr_units" / "section" / "first" / "p1" / "page.png"))
        self.assertLess(fitz.Pixmap(str(zoom_png)).width, full_png.width)
        third = self.units_file("third.json", [
            {"unit_id": "r1_third", "kind": "region", "page": 1, "read_kind": "third",
             "bbox_pt": [100, 100, 300, 160], "parent_unit_id": "r1"},
        ])
        code, _ = self.run_batch(source, third)
        self.assertEqual(code, 0)
        state = self.state_of(source)
        third_attempt = state["units"]["r1_third"]["attempts"][-1]
        parent_crop = state["units"]["r1"]["attempts"][-1]["png"]
        self.assertEqual(Path(third_attempt["png"]), Path(parent_crop))
        self.assertTrue(third_attempt["reused_image"])
        self.assertEqual(state["reads"]["third"], 1)

    def test_failed_unit_partial_then_resume_fills_only_failures(self):
        source = self.textbook_source()
        units = self.units_file("u1.json", [
            {"unit_id": "p1", "kind": "page", "page": 1, "read_kind": "first"},
            {"unit_id": "flaky-1", "kind": "page", "page": 2, "read_kind": "first"},
        ])
        code, err = self.run_batch(source, units)
        self.assertEqual(code, 0)
        result = parse_lines(err, "RESULT")[-1]
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["units_ok"], 1)
        state = self.state_of(source)
        self.assertEqual(state["units"]["flaky-1"]["status"], "failed")

        # rerun: only the failed unit gets a new vision call
        code, err = self.run_batch(source, units)
        self.assertEqual(code, 0)
        result = parse_lines(err, "RESULT")[-1]
        self.assertEqual((result["status"], result["done"], result["total"]), ("ok", 1, 1))
        self.assertEqual(call_count(self.probe, "p1/page.png"), 1)
        self.assertEqual(call_count(self.probe, "flaky-1/"), 1)
        state = self.state_of(source)
        self.assertEqual(state["units"]["flaky-1"]["attempts"][-1]["status"], "ok")

    def test_lost_result_file_refills_without_rerender(self):
        source = self.textbook_source()
        units = self.units_file("u1.json", [
            {"unit_id": "p1", "kind": "page", "page": 1, "read_kind": "first"},
        ])
        code, _ = self.run_batch(source, units)
        self.assertEqual(code, 0)
        state = self.state_of(source)
        ocr_path = Path(state["units"]["p1"]["attempts"][-1]["ocr"])
        png_path = Path(state["units"]["p1"]["attempts"][-1]["png"])
        ocr_path.unlink()
        before_render = png_path.stat().st_mtime_ns
        code, _ = self.run_batch(source, units)
        self.assertEqual(code, 0)
        self.assertEqual(call_count(self.probe, "p1/page.png"), 2)
        self.assertEqual(png_path.stat().st_mtime_ns, before_render)
        state = self.state_of(source)
        self.assertEqual(len(state["units"]["p1"]["attempts"]), 2)

    def test_t4_status_lines_readable_by_read_status(self):
        source = self.textbook_source()
        units = self.units_file("u1.json", [
            {"unit_id": "p1", "kind": "page", "page": 1, "read_kind": "first"},
            {"unit_id": "p2", "kind": "page", "page": 2, "read_kind": "first"},
        ])
        log = self.root / "run.log"
        env = dict(os.environ, OCR_REFRESH_TEST_PROBE=str(self.probe))
        with open(log, "w") as handle:
            proc = subprocess.run([
                sys.executable, str(R_PATH), "run",
                "--target", str(source), "--pdf", str(self.pdf),
                "--units", str(units),
                "--state", str(source) + ".ocr_repair_state.json",
                "--glance", str(self.glance),
            ], stdout=subprocess.DEVNULL, stderr=handle, env=env)
        self.assertEqual(proc.returncode, 0)
        poll = subprocess.run(
            [sys.executable, str(READ_STATUS), str(log)],
            capture_output=True, text=True,
        )
        line = poll.stdout.strip()
        self.assertTrue(line.startswith("RESULT "), line)
        payload = json.loads(line[len("RESULT "):])
        for key in ("status", "phase", "done", "total", "failed", "elapsed_s"):
            self.assertIn(key, payload)
        self.assertEqual((payload["done"], payload["total"], payload["failed"]), (2, 2, 0))
        progress_lines = [l for l in log.read_text().splitlines() if l.startswith("PROGRESS ")]
        self.assertTrue(progress_lines)

    def test_actual_model_recorded_from_call_info(self):
        source = self.textbook_source()
        units = self.units_file("u1.json", [
            {"unit_id": "p1", "kind": "page", "page": 1, "read_kind": "first"},
        ])
        code, _ = self.run_batch(source, units)
        self.assertEqual(code, 0)
        attempt = self.state_of(source)["units"]["p1"]["attempts"][-1]
        self.assertEqual(attempt["model"], "fake-model")
        self.assertEqual(attempt["chain_name"], "fake-chain")
        self.assertEqual(attempt["tier"], "free")

    def test_structural_lines_fail_the_unit(self):
        source = self.textbook_source()
        units = self.units_file("u1.json", [
            {"unit_id": "struct-1", "kind": "page", "page": 1, "read_kind": "first"},
        ])
        code, err = self.run_batch(source, units)
        self.assertEqual(code, 0)
        result = parse_lines(err, "RESULT")[-1]
        self.assertEqual(result["failed"], 1)
        state = self.state_of(source)
        self.assertEqual(state["units"]["struct-1"]["error"], "structural_lines_in_ocr")

    def test_textbook_finalize_patches_one_page_and_preserves_the_rest(self):
        source = self.textbook_source()
        before = source.read_bytes()
        units = self.units_file("u1.json", [
            {"unit_id": "p1", "kind": "page", "page": 1, "read_kind": "first"},
        ])
        self.run_batch(source, units)
        self.assertEqual(source.read_bytes(), before, "run must not touch source")

        accept = self.accept_file("acc.json", ["p1"], [], source)
        code, err = self.run_r([
            "finalize", "--state", str(source) + ".ocr_repair_state.json",
            "--accept", str(accept),
        ])
        self.assertEqual(code, 0, err)
        text = source.read_text(encoding="utf-8")
        self.assertIn("llm_ocr: true", text)
        self.assertIn("llm_ocr_model: \"fake-model\"", text)
        self.assertIn("title: fixture", text)
        self.assertIn("原书页码: [11, 12]", text)
        self.assertIn("OCR[page.png@p1]", text)
        self.assertIn("[图 p.11-1: fixture 旧图描述，必须保留]", text)
        self.assertIn("第二页原文 keep-me-line", text)
        self.assertNotIn("旧乱码 ααα", text)
        self.assertNotIn("keep-me-line\n### 原书", text)

    def test_kakomon_finalize_preserves_markers(self):
        source = self.kakomon_source()
        units = self.units_file("u1.json", [
            {"unit_id": "p1", "kind": "page", "page": 1, "read_kind": "first"},
        ], target=source)
        self.run_batch(source, units)
        accept = self.accept_file("acc.json", ["p1"], [], source)
        code, err = self.run_r([
            "finalize", "--state", str(source) + ".ocr_repair_state.json",
            "--accept", str(accept),
        ])
        self.assertEqual(code, 0, err)
        text = source.read_text(encoding="utf-8")
        self.assertEqual(text.count("<!-- PDF_PAGE:"), 2)
        self.assertIn("school: fixture", text)
        self.assertIn("OCR[page.png@p1]", text)
        self.assertIn("第二页原题 keep-me-line", text)
        self.assertNotIn("旧乱码 βββ", text)

    def test_finalize_gates_keep_source_unchanged(self):
        source = self.textbook_source()
        before = source.read_bytes()
        units = self.units_file("u1.json", [
            {"unit_id": "p1", "kind": "page", "page": 1, "read_kind": "first"},
            {"unit_id": "p2", "kind": "page", "page": 2, "read_kind": "first"},
        ])
        self.run_batch(source, units)
        state_path = str(source) + ".ocr_repair_state.json"
        accept = self.accept_file("acc.json", ["p1"], [], source)
        code, err = self.run_r(["finalize", "--state", state_path, "--accept", str(accept)])
        self.assertEqual(code, 1)
        self.assertEqual(parse_lines(err, "ERROR")[-1]["code"], "unresolved_units")
        self.assertEqual(source.read_bytes(), before)

        accept = self.accept_file("acc.json", ["p1", "p2"], [], source)
        # break p2's ocr file so its result no longer verifies
        state = self.state_of(source)
        Path(state["units"]["p2"]["attempts"][-1]["ocr"]).unlink()
        code, err = self.run_r(["finalize", "--state", state_path, "--accept", str(accept)])
        self.assertEqual(code, 1)
        self.assertEqual(parse_lines(err, "ERROR")[-1]["code"], "accept_failed_unit")
        self.assertEqual(source.read_bytes(), before)

    def test_finalize_rejects_source_drift(self):
        source = self.textbook_source()
        units = self.units_file("u1.json", [
            {"unit_id": "p1", "kind": "page", "page": 1, "read_kind": "first"},
        ])
        self.run_batch(source, units)
        source.write_text(source.read_text(encoding="utf-8") + "drift\n", encoding="utf-8")
        accept = self.accept_file("acc.json", ["p1"], [], source)
        code, err = self.run_r([
            "finalize", "--state", str(source) + ".ocr_repair_state.json",
            "--accept", str(accept),
        ])
        self.assertEqual(code, 1)
        self.assertEqual(parse_lines(err, "ERROR")[-1]["code"], "source_changed")

    def test_region_unlocatable_then_page_upgrade(self):
        source = self.textbook_source()
        # page 2 body has only one line; duplicate it to make anchor_text ambiguous
        source.write_text(
            source.read_text(encoding="utf-8").replace(
                "第二页原文 keep-me-line\n", "dup line\ndup line\n"
            ),
            encoding="utf-8",
        )
        before = source.read_bytes()
        units = self.units_file("u1.json", [
            {"unit_id": "r2", "kind": "region", "page": 2, "read_kind": "first",
             "bbox_pt": [100, 100, 300, 160], "anchor_text": "dup line"},
        ])
        code, _ = self.run_batch(source, units)
        self.assertEqual(code, 0)
        accept = self.accept_file("acc.json", ["r2"], [], source)
        code, err = self.run_r([
            "finalize", "--state", str(source) + ".ocr_repair_state.json",
            "--accept", str(accept),
        ])
        self.assertEqual(code, 1)
        self.assertEqual(parse_lines(err, "ERROR")[-1]["code"], "region_unlocatable")
        self.assertEqual(source.read_bytes(), before)

        # page-level upgrade plan: region resolved, page accepted
        page_units = self.units_file("p2.json", [
            {"unit_id": "p2", "kind": "page", "page": 2, "read_kind": "first"},
        ])
        code, _ = self.run_batch(source, page_units)
        self.assertEqual(code, 0)
        accept = self.accept_file("acc.json", ["p2"], ["r2"], source)
        code, err = self.run_r([
            "finalize", "--state", str(source) + ".ocr_repair_state.json",
            "--accept", str(accept),
        ])
        self.assertEqual(code, 0, err)
        text = source.read_text(encoding="utf-8")
        self.assertNotIn("dup line", text)
        self.assertIn("OCR[page.png@p2]", text)

    def test_region_line_patch_replaces_only_anchor_line(self):
        source = self.textbook_source()
        units = self.units_file("u1.json", [
            {"unit_id": "r1", "kind": "region", "page": 2, "read_kind": "first",
             "bbox_pt": [100, 100, 300, 160], "anchor_text": "第二页原文 keep-me-line"},
        ])
        code, _ = self.run_batch(source, units)
        self.assertEqual(code, 0)
        accept = self.accept_file("acc.json", ["r1"], [], source)
        code, err = self.run_r([
            "finalize", "--state", str(source) + ".ocr_repair_state.json",
            "--accept", str(accept),
        ])
        self.assertEqual(code, 0, err)
        text = source.read_text(encoding="utf-8")
        self.assertIn("OCR[crop.png@r1]", text)
        self.assertIn("### 原书 p.12（印刷页 2）", text)
        self.assertIn("旧乱码 ααα", text)  # other pages untouched

    def test_no_image_description_calls_and_existing_figures_carried(self):
        source = self.textbook_source()
        units = self.units_file("u1.json", [
            {"unit_id": "p1", "kind": "page", "page": 1, "read_kind": "first"},
        ])
        code, _ = self.run_batch(source, units)
        self.assertEqual(code, 0)
        # every probe call was an --ocr call (fake exits 3 otherwise)
        for record in probe_calls(self.probe):
            self.assertIn("page.png", record["png"])
        accept = self.accept_file("acc.json", ["p1"], [], source)
        code, _ = self.run_r([
            "finalize", "--state", str(source) + ".ocr_repair_state.json",
            "--accept", str(accept),
        ])
        self.assertEqual(code, 0)
        text = source.read_text(encoding="utf-8")
        self.assertIn("[图 p.11-1: fixture 旧图描述，必须保留]", text)

    def test_topology_two_glance_subprocesses_overlap(self):
        source = self.textbook_source()
        units = self.units_file("u1.json", [
            {"unit_id": "p1", "kind": "page", "page": 1, "read_kind": "first"},
            {"unit_id": "p2", "kind": "page", "page": 2, "read_kind": "first"},
        ])
        os.environ["OCR_REFRESH_TEST_SLEEP"] = "0.7"
        try:
            code, err = self.run_batch(source, units, concurrency=2)
        finally:
            os.environ.pop("OCR_REFRESH_TEST_SLEEP", None)
        self.assertEqual(code, 0, err)
        ends = [r for r in probe_calls(self.probe) if "t1" in r]
        starts = [r for r in probe_calls(self.probe) if "t1" not in r]
        self.assertEqual(len(ends), 2)
        # the two child processes overlapped in time: scheduling works and
        # results stay isolated per unit. This proves topology, not provider
        # capacity.
        started_sorted = sorted(s["t0"] for s in starts)
        finished_sorted = sorted(f["t1"] for f in ends)
        self.assertLess(started_sorted[1], finished_sorted[0])
        root = self.root / ".ocr_units" / "section" / "u1"
        self.assertTrue((root / "p1" / "attempt1.ocr.md").is_file())
        self.assertTrue((root / "p2" / "attempt1.ocr.md").is_file())
        self.assertNotEqual(
            (root / "p1" / "attempt1.ocr.md").read_text(),
            (root / "p2" / "attempt1.ocr.md").read_text(),
        )


if __name__ == "__main__":
    unittest.main()
