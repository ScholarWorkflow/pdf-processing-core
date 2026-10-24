"""No-model regression tests for the formula-repair state machine."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path


RUNNER = Path(__file__).parents[1] / "formula_repair_runner.py"
SPEC = importlib.util.spec_from_file_location("formula_repair_runner", RUNNER)
runner = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(runner)


class FormulaRepairRunnerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "book"
        self.root.mkdir()
        self.pdf = self.root / "section.pdf"
        self.pdf.write_bytes(b"%PDF fake")
        self.source = self.root / "text.md"
        self.source.write_text("---\nllm_ocr: false\n---\nold", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def _faudit(self, pages):
        self.pdf.with_suffix(".faudit.json").write_text(json.dumps({
            "fingerprint": "pdf-fingerprint",
            "pages": pages,
            "report": {"sections": []},
            "summary": {},
        }), encoding="utf-8")

    def _plan(self, answer_check=False):
        return runner.plan(Namespace(pdf=str(self.pdf), source=str(self.source), root=str(self.root),
                                     answer_check=answer_check, force=False))

    def _marker_source(self):
        folder = self.root / "extraction" / "chapter" / "section"
        folder.mkdir(parents=True)
        self.source = folder / "text.md"
        self.source.write_text("---\nllm_ocr: false\n---\n<!-- PDF_PAGE: 1 -->\nfirst\n"
                               "<!-- PDF_PAGE: 2 -->\nsecond", encoding="utf-8")

    def test_marker_boundaries_preserve_region_plan(self):
        self._marker_source()
        self._faudit({str(i): {"page_verdict": "suspect", "verdicts": [
            {"verdict": "suspect", "bbox_pt": [0, 0, 5, 5]}]} for i in (1, 2)})
        self._plan()
        jobs = list(runner._load_state(self.root)["jobs"].values())
        self.assertEqual([j["kind"] for j in jobs], ["region"])
        self.assertEqual([m["page"] for m in jobs[0]["members"]], [1, 2])

    def test_marker_boundaries_allow_partial_collect(self):
        self._marker_source()
        self._faudit({str(i): {"page_verdict": "unverified", "verdicts": []} for i in (1, 2)})
        self._plan()
        claimed = runner.claim(Namespace(root=str(self.root)))
        job = runner._load_state(self.root)["jobs"][claimed["job_id"]]
        Path(job["unit_dir"], "RESULT.json").write_text(json.dumps({
            "job_id": job["job_id"], "status": "failed", "completed_members": [{"page": 1}],
            "failed_members": [{"page": 2}], "error_code": "ocr_incomplete",
        }), encoding="utf-8")
        runner.collect(Namespace(root=str(self.root)))
        updated = runner._load_state(self.root)["jobs"][job["job_id"]]
        self.assertEqual(updated["members"], [{"page": 2}])
        self.assertEqual(updated["completed_member_count"], 1)
        # A leading subset is not a complete mapping of the paired two-page PDF.
        self.source.write_text("---\nllm_ocr: false\n---\n<!-- PDF_PAGE: 1 -->\nfirst\nsecond")
        job["status"] = "running"
        job["members"] = [{"page": 1}, {"page": 2}]
        state = runner._load_state(self.root)
        state["jobs"][claimed["job_id"]] = job
        runner._save_state(self.root, state)
        runner.collect(Namespace(root=str(self.root)))
        updated = runner._load_state(self.root)["jobs"][job["job_id"]]
        self.assertEqual(updated["members"], [{"page": 1}, {"page": 2}])

    def test_page_jobs_are_capped_at_twelve(self):
        self._faudit({str(i): {"page_verdict": "unverified", "verdicts": []} for i in range(1, 18)})
        self._plan()
        state = runner._load_state(self.root)
        jobs = list(state["jobs"].values())
        self.assertEqual([len(job["members"]) for job in jobs], [12, 5])

    def test_answer_jobs_are_capped_at_eight(self):
        self._faudit({str(i): {"page_verdict": "unverified", "verdicts": []} for i in range(1, 10)})
        self._plan(answer_check=True)
        state = runner._load_state(self.root)
        self.assertEqual([len(job["members"]) for job in state["jobs"].values()], [8, 1])

    def test_region_jobs_are_capped_at_twenty(self):
        verdicts = [{"bbox_pt": [i, 1, i + 1, 2], "verdict": "suspect"} for i in range(21)]
        self._faudit({"1": {"page_verdict": "suspect", "verdicts": verdicts}})
        self._plan()
        state = runner._load_state(self.root)
        self.assertEqual([len(job["members"]) for job in state["jobs"].values()], [20, 1])

    def test_interrupted_job_keeps_attempt_and_degrades_on_third(self):
        self._faudit({"1": {"page_verdict": "unverified", "verdicts": []}})
        self._plan()
        runner.claim(Namespace(root=str(self.root)))
        runner.recover(Namespace(root=str(self.root)))
        state = runner._load_state(self.root)
        job = next(iter(state["jobs"].values()))
        self.assertEqual((job["status"], job["attempts"]), ("pending", 1))
        for _ in range(2):
            runner.claim(Namespace(root=str(self.root)))
            runner.recover(Namespace(root=str(self.root)))
        state = runner._load_state(self.root)
        job = next(iter(state["jobs"].values()))
        self.assertEqual((job["status"], job["attempts"]), ("degraded", 3))

    def test_worker_result_is_the_only_worker_to_runner_channel(self):
        self._faudit({"1": {"page_verdict": "unverified", "verdicts": []}})
        self._plan()
        claimed = runner.claim(Namespace(root=str(self.root)))
        state = runner._load_state(self.root)
        job = state["jobs"][claimed["job_id"]]
        Path(job["unit_dir"], "RESULT.json").write_text(json.dumps({
            "job_id": job["job_id"], "status": "ok",
            "completed_members": job["members"], "completed_units": 1,
        }), encoding="utf-8")
        runner.collect(Namespace(root=str(self.root)))
        self.assertEqual(runner._load_state(self.root)["jobs"][job["job_id"]]["status"], "ok")

    def test_incomplete_success_result_is_not_accepted(self):
        self._faudit({"1": {"page_verdict": "unverified", "verdicts": []}})
        self._plan()
        claimed = runner.claim(Namespace(root=str(self.root)))
        state = runner._load_state(self.root)
        job = state["jobs"][claimed["job_id"]]
        Path(job["unit_dir"], "RESULT.json").write_text(json.dumps({
            "job_id": job["job_id"], "status": "ok", "patches": [],
        }), encoding="utf-8")

        runner.collect(Namespace(root=str(self.root)))

        updated = runner._load_state(self.root)["jobs"][job["job_id"]]
        self.assertEqual(updated["status"], "pending")
        self.assertEqual(updated["last_error"], "worker_incomplete")

    def test_finalize_refuses_to_overwrite_changed_source(self):
        self._faudit({"1": {"page_verdict": "unverified", "verdicts": []}})
        self._plan()
        claimed = runner.claim(Namespace(root=str(self.root)))
        state = runner._load_state(self.root)
        job = state["jobs"][claimed["job_id"]]
        Path(job["unit_dir"], "RESULT.json").write_text(json.dumps({
            "job_id": job["job_id"], "status": "ok", "completed_members": job["members"],
            "patches": [{"start": 22, "end": 25, "replacement": "new"}],
        }), encoding="utf-8")
        runner.collect(Namespace(root=str(self.root)))
        self.source.write_text("external edit", encoding="utf-8")
        result = runner.finalize(Namespace(root=str(self.root), pdf=str(self.pdf), audit_command=None, dry_run=False))
        self.assertEqual(result["status"], "replanned")
        self.assertEqual(self.source.read_text(encoding="utf-8"), "external edit")

    def test_unmeasured_profile_uses_one_lane_one_job(self):
        self._faudit({str(i): {"page_verdict": "unverified", "verdicts": []} for i in range(1, 14)})
        self._plan()
        result = runner.queue(Namespace(root=str(self.root), profile="not-measured"))
        self.assertEqual((result["workers"], result["jobs_per_lane"], result["jobs"]), (1, 1, 1))

    def test_lane_queue_never_assigns_one_pdf_to_two_lanes(self):
        self._faudit({str(i): {"page_verdict": "unverified", "verdicts": []} for i in range(1, 26)})
        self._plan()
        benchmark = runner._json(runner.BENCHMARK_PATH, {})
        original = json.dumps(benchmark)
        try:
            benchmark["profiles"] = {"test-profile": {"workers": 2, "jobs_per_lane": 2}}
            runner._write_json(runner.BENCHMARK_PATH, benchmark)
            result = runner.queue(Namespace(root=str(self.root), profile="test-profile"))
            queues = [runner._json(Path(path), {})["jobs"] for path in result["queue_files"]]
            self.assertEqual(len(queues), 1)
            self.assertEqual(len(queues[0]), 2)
            self.assertTrue(all("attempts_at_queue" in entry for entry in queues[0]))
        finally:
            runner._write_json(runner.BENCHMARK_PATH, json.loads(original))

    def test_queue_splits_distinct_pdfs_fairly_across_lanes(self):
        second_pdf = self.root / "other.pdf"
        second_pdf.write_bytes(b"%PDF fake2")
        self._faudit({str(i): {"page_verdict": "unverified", "verdicts": []} for i in range(1, 14)})
        second_pdf.with_suffix(".faudit.json").write_text(json.dumps({
            "fingerprint": "fp2", "pages": {"1": {"page_verdict": "unverified", "verdicts": []},
                                            "2": {"page_verdict": "unverified", "verdicts": []}},
            "report": {"sections": []}, "summary": {},
        }), encoding="utf-8")
        runner.plan(Namespace(pdf=str(self.pdf), source=str(self.source), root=str(self.root),
                              answer_check=False, force=False))
        other_source = self.root / "other.md"
        other_source.write_text("---\nllm_ocr: false\n---\nold", encoding="utf-8")
        runner.plan(Namespace(pdf=str(second_pdf), source=str(other_source), root=str(self.root),
                              answer_check=False, force=False))
        benchmark = runner._json(runner.BENCHMARK_PATH, {})
        original = json.dumps(benchmark)
        try:
            benchmark["profiles"] = {"test-profile": {"workers": 2, "jobs_per_lane": 2}}
            runner._write_json(runner.BENCHMARK_PATH, benchmark)
            result = runner.queue(Namespace(root=str(self.root), profile="test-profile"))
            self.assertEqual(len(result["queue_files"]), 2)
            queues = [runner._json(Path(path), {})["jobs"] for path in result["queue_files"]]
            pdfs_per_lane = [{entry["job_id"].rsplit(":", 1)[0] for entry in lane} for lane in queues]
            # Fair round: each lane carries exactly one distinct PDF.
            self.assertEqual(len(pdfs_per_lane[0]), 1)
            self.assertEqual(len(pdfs_per_lane[1]), 1)
            self.assertNotIn(list(pdfs_per_lane[0])[0], pdfs_per_lane[1])
        finally:
            runner._write_json(runner.BENCHMARK_PATH, json.loads(original))

    def test_lane_next_claims_first_pending_job_and_reports_done(self):
        self._faudit({"1": {"page_verdict": "unverified", "verdicts": []}})
        self._plan()
        result = runner.queue(Namespace(root=str(self.root), profile=None))
        lane_file = result["queue_files"][0]
        step = runner.lane_next(Namespace(root=str(self.root), lane=lane_file))
        self.assertEqual(step["action"], "claimed")
        state = runner._load_state(self.root)
        job_id = step["job_id"]
        self.assertEqual(state["jobs"][job_id]["status"], "running")
        Path(step["job_file"])  # job.json written for the worker
        self.assertTrue(Path(step["job_file"]).is_file())
        # finish the job -> lane reports done
        Path(state["jobs"][job_id]["unit_dir"], "RESULT.json").write_text(json.dumps({
            "job_id": job_id, "status": "ok", "completed_members": state["jobs"][job_id]["members"],
            "finished_monotonic_ns": 5,
        }), encoding="utf-8")
        runner.collect(Namespace(root=str(self.root)))
        self.assertEqual(runner.lane_next(Namespace(root=str(self.root), lane=lane_file))["action"], "done")

    def test_lane_next_stops_lane_after_job_failure_and_keeps_rest_pending(self):
        self._faudit({str(i): {"page_verdict": "unverified", "verdicts": []} for i in range(1, 26)})
        self._plan()
        benchmark = runner._json(runner.BENCHMARK_PATH, {})
        original = json.dumps(benchmark)
        try:
            benchmark["profiles"] = {"test-profile": {"workers": 1, "jobs_per_lane": 2}}
            runner._write_json(runner.BENCHMARK_PATH, benchmark)
            result = runner.queue(Namespace(root=str(self.root), profile="test-profile"))
        finally:
            runner._write_json(runner.BENCHMARK_PATH, json.loads(original))
        lane_file = result["queue_files"][0]
        first = runner.lane_next(Namespace(root=str(self.root), lane=lane_file))
        self.assertEqual(first["action"], "claimed")
        state = runner._load_state(self.root)
        Path(state["jobs"][first["job_id"]]["unit_dir"], "RESULT.json").write_text(json.dumps({
            "job_id": first["job_id"], "status": "failed", "completed_members": [],
            "failed_members": state["jobs"][first["job_id"]]["members"], "error_code": "ocr_incomplete",
        }), encoding="utf-8")
        runner.collect(Namespace(root=str(self.root)))
        step = runner.lane_next(Namespace(root=str(self.root), lane=lane_file))
        self.assertEqual(step["action"], "lane_stopped")
        # remaining queue job untouched: still pending, never claimed
        state = runner._load_state(self.root)
        lane_entries = runner._json(Path(lane_file), {})["jobs"]
        second_job = state["jobs"][lane_entries[1]["job_id"]]
        self.assertEqual(second_job["status"], "pending")
        self.assertNotIn("unit_dir", second_job)
        self.assertEqual(second_job.get("attempts", 0), 0)

    def test_lane_next_awaits_result_for_running_job(self):
        self._faudit({"1": {"page_verdict": "unverified", "verdicts": []}})
        self._plan()
        result = runner.queue(Namespace(root=str(self.root), profile=None))
        lane_file = result["queue_files"][0]
        runner.lane_next(Namespace(root=str(self.root), lane=lane_file))
        step = runner.lane_next(Namespace(root=str(self.root), lane=lane_file))
        self.assertEqual(step["action"], "await_result")
        self.assertTrue(step["job_file"].endswith("job.json"))

    def test_collect_requires_result_timestamp_for_job_elapsed(self):
        self._faudit({"1": {"page_verdict": "unverified", "verdicts": []}})
        self._plan()
        claimed = runner.claim(Namespace(root=str(self.root)))
        state = runner._load_state(self.root)
        job = state["jobs"][claimed["job_id"]]
        Path(job["unit_dir"], "RESULT.json").write_text(json.dumps({
            "job_id": job["job_id"], "status": "ok", "completed_members": job["members"],
        }), encoding="utf-8")
        runner.collect(Namespace(root=str(self.root)))
        updated = runner._load_state(self.root)["jobs"][job["job_id"]]
        self.assertEqual(updated["status"], "ok")
        self.assertNotIn("completed_monotonic_ns", updated)
        self.assertEqual(updated["timing_contract"], "missing_finished_monotonic_ns")
        summary = runner.status(Namespace(root=str(self.root)))
        self.assertEqual(summary["job_elapsed_ms"], [])

    def test_collect_persists_short_worker_counters(self):
        self._faudit({"1": {"page_verdict": "unverified", "verdicts": []}})
        self._plan()
        claimed = runner.claim(Namespace(root=str(self.root)))
        state = runner._load_state(self.root)
        job = state["jobs"][claimed["job_id"]]
        Path(job["unit_dir"], "RESULT.json").write_text(json.dumps({
            "job_id": job["job_id"], "status": "ok", "completed_members": job["members"],
            "finished_monotonic_ns": 9,
            "ocr_pages": 1, "second_reads": 2, "events": {"rate_limit_429": 1, "fallback": 0},
        }), encoding="utf-8")
        runner.collect(Namespace(root=str(self.root)))
        updated = runner._load_state(self.root)["jobs"][job["job_id"]]
        self.assertEqual((updated["ocr_pages"], updated["second_reads"]), (1, 2))
        self.assertEqual(updated["worker_events"], {"rate_limit_429": 1, "fallback": 0})

    def test_source_change_replan_resets_stale_ok_job_without_consuming_attempts(self):
        self._faudit({"1": {"page_verdict": "unverified", "verdicts": []}})
        self._plan()
        claimed = runner.claim(Namespace(root=str(self.root)))
        state = runner._load_state(self.root)
        job = state["jobs"][claimed["job_id"]]
        Path(job["unit_dir"], "RESULT.json").write_text(json.dumps({
            "job_id": job["job_id"], "status": "ok", "completed_members": job["members"],
            "finished_monotonic_ns": 7,
            "patches": [{"start": 22, "end": 25, "replacement": "new"}],
        }), encoding="utf-8")
        runner.collect(Namespace(root=str(self.root)))
        self.source.write_text("---\nllm_ocr: false\n---\nexternal edit", encoding="utf-8")
        result = runner.finalize(Namespace(root=str(self.root), pdf=str(self.pdf), audit_command=None, dry_run=False))
        self.assertEqual(result["status"], "replanned")
        replan = self._plan()
        self.assertEqual(replan["jobs_planned"], 1)
        state = runner._load_state(self.root)
        job = state["jobs"][claimed["job_id"]]
        self.assertEqual(job["status"], "pending")
        self.assertEqual(job["attempts"], 1)  # replan never consumes a retry
        self.assertEqual(job["last_error"], "source_changed")
        # source content untouched by the refused merge
        self.assertEqual(self.source.read_text(encoding="utf-8"), "---\nllm_ocr: false\n---\nexternal edit")

    def test_replan_preserves_shrunk_members_and_attempts(self):
        self._faudit({str(i): {"page_verdict": "unverified", "verdicts": []} for i in range(1, 4)})
        self._plan()
        claimed = runner.claim(Namespace(root=str(self.root)))
        state = runner._load_state(self.root)
        job = state["jobs"][claimed["job_id"]]
        Path(job["unit_dir"], "RESULT.json").write_text(json.dumps({
            "job_id": job["job_id"], "status": "failed", "completed_members": [{"page": 1}],
            "failed_members": [{"page": 2}, {"page": 3}], "error_code": "ocr_incomplete",
        }), encoding="utf-8")
        runner.collect(Namespace(root=str(self.root)))
        self._plan()  # dispatcher replans (e.g. new queue round)
        updated = runner._load_state(self.root)["jobs"][job["job_id"]]
        self.assertEqual(updated["status"], "pending")
        self.assertEqual(updated["members"], [{"page": 2}, {"page": 3}])
        self.assertEqual(updated["attempts"], 1)

    def test_pending_l3_regions_never_become_ocr_jobs(self):
        verdicts = [
            {"bbox_pt": [0, 0, 10, 10], "text": "a^b", "verdict": "suspect", "signals": []},
            {"bbox_pt": [0, 20, 10, 30], "text": "x = y + 1", "verdict": "pending_l3", "signals": []},
        ]
        self._faudit({"1": {"page_verdict": "suspect", "verdicts": verdicts}})
        self._plan()
        state = runner._load_state(self.root)
        job = next(iter(state["jobs"].values()))
        self.assertEqual(job["members"], [{"page": 1, "bbox_pt": [0, 0, 10, 10]}])

    def test_plan_registers_l3_cleared_pdf_without_jobs(self):
        self._faudit({"1": {"page_verdict": "pending_l3",
                            "verdicts": [{"bbox_pt": [0, 0, 5, 5], "text": "x = y", "verdict": "pending_l3", "signals": []}]}})
        result = self._plan()
        self.assertEqual(result["jobs_planned"], 0)
        state = runner._load_state(self.root)
        self.assertEqual(state["pdfs"][str(self.pdf.resolve())]["status"], "pending")
        # triage clears every pending_l3 -> sidecar aggregate becomes ok
        self._faudit({"1": {"page_verdict": "ok", "verdicts": []}})
        self._plan()
        state = runner._load_state(self.root)
        aggregate = state["pdfs"][str(self.pdf.resolve())]
        self.assertEqual((aggregate["status"], aggregate["completion_origin"]), ("ok", "no_repair_needed"))
        self.assertEqual(state["jobs"], {})

    def test_legacy_verified_target_is_registered_without_jobs(self):
        self.source.write_text("---\nllm_ocr: true\n---\nold", encoding="utf-8")
        self._faudit({"1": {"page_verdict": "ok", "verdicts": []}})
        result = self._plan()
        self.assertEqual(result["status"], "skipped_ok")
        state = runner._load_state(self.root)
        aggregate = state["pdfs"][str(self.pdf.resolve())]
        self.assertEqual((aggregate["status"], aggregate["completion_origin"]), ("skipped_ok", "legacy_verified"))

    def test_finalize_adds_llm_ocr_only_at_the_final_merge(self):
        self._faudit({"1": {"page_verdict": "unverified", "verdicts": []}})
        self._plan()
        claimed = runner.claim(Namespace(root=str(self.root)))
        state = runner._load_state(self.root)
        job = state["jobs"][claimed["job_id"]]
        Path(job["unit_dir"], "RESULT.json").write_text(json.dumps({
            "job_id": job["job_id"], "status": "ok", "completed_members": job["members"],
            "patches": [],
        }), encoding="utf-8")
        runner.collect(Namespace(root=str(self.root)))
        result = runner.finalize(Namespace(root=str(self.root), pdf=str(self.pdf), audit_command=None, dry_run=False))
        self.assertEqual(result["status"], "ok")
        self.assertIn("llm_ocr: true", self.source.read_text(encoding="utf-8"))

    def test_status_only_returns_aggregated_state(self):
        self._faudit({"1": {"page_verdict": "unverified", "verdicts": []}})
        self._plan()
        summary = runner.status(Namespace(root=str(self.root)))
        self.assertEqual(summary["job_counts"], {"pending": 1})
        self.assertNotIn("jobs", summary)

    def test_audit_command_uses_released_package_module(self):
        command = runner._audit_command(self.pdf, self.source, False)
        self.assertEqual(command[:3], [runner.sys.executable, "-m", "pdfx.cli"])
        self.assertNotIn(str(Path("lib") / "pdfx" / "cli.py"), command)

    def test_partial_worker_result_retries_only_failed_members(self):
        self._faudit({str(i): {"page_verdict": "unverified", "verdicts": []} for i in range(1, 4)})
        self._plan()
        claimed = runner.claim(Namespace(root=str(self.root)))
        state = runner._load_state(self.root)
        job = state["jobs"][claimed["job_id"]]
        Path(job["unit_dir"], "RESULT.json").write_text(json.dumps({
            "job_id": job["job_id"], "status": "failed", "completed_members": [{"page": 1}],
            "failed_members": [{"page": 2}, {"page": 3}], "error_code": "ocr_incomplete",
        }), encoding="utf-8")
        runner.collect(Namespace(root=str(self.root)))
        updated = runner._load_state(self.root)["jobs"][job["job_id"]]
        self.assertEqual(updated["status"], "pending")
        self.assertEqual(updated["members"], [{"page": 2}, {"page": 3}])
        self.assertEqual(updated["completed_member_count"], 1)

    def test_setup_failure_does_not_consume_ocr_attempt(self):
        self._faudit({"1": {"page_verdict": "unverified", "verdicts": []}})
        self._plan()
        claimed = runner.claim(Namespace(root=str(self.root)))
        state = runner._load_state(self.root)
        job = state["jobs"][claimed["job_id"]]
        Path(job["unit_dir"], "RESULT.json").write_text(json.dumps({
            "job_id": job["job_id"], "status": "failed", "error_code": "missing_page_delimiters",
        }), encoding="utf-8")
        runner.collect(Namespace(root=str(self.root)))
        updated = runner._load_state(self.root)["jobs"][job["job_id"]]
        self.assertEqual((updated["status"], updated["attempts"]), ("pending", 0))

    def test_standard_audit_command_uses_released_package_and_correct_extraction_root(self):
        textbook_source = Path("/tmp/book/extraction/chapter/section/text.md")
        command = runner._audit_command(Path("/tmp/book/split_pdfs/section.pdf"), textbook_source, True)
        self.assertEqual(command[:3], [runner.sys.executable, "-m", "pdfx.cli"])
        self.assertIn("--extraction-dir", command)
        self.assertIn("/tmp/book/extraction", command)

    def test_status_reports_completed_job_elapsed_time_only(self):
        self._faudit({"1": {"page_verdict": "unverified", "verdicts": []}})
        self._plan()
        claimed = runner.claim(Namespace(root=str(self.root)))
        state = runner._load_state(self.root)
        job = state["jobs"][claimed["job_id"]]
        job["started_monotonic_ns"] = 100_000_000
        runner._save_state(self.root, state)
        Path(job["unit_dir"], "RESULT.json").write_text(json.dumps({
            "job_id": job["job_id"], "status": "ok", "completed_members": job["members"],
            "finished_monotonic_ns": 350_000_000,
        }), encoding="utf-8")
        runner.collect(Namespace(root=str(self.root)))
        summary = runner.status(Namespace(root=str(self.root)))
        self.assertEqual(summary["job_elapsed_ms"], [{"job_id": job["job_id"], "elapsed_ms": 250.0}])


class PrepareHeadingsTest(unittest.TestCase):
    def setUp(self):
        import pymupdf
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pdf = self.root / "section.pdf"
        self.source = self.root / "text.md"
        self.fitz = pymupdf

    def tearDown(self):
        self.temp.cleanup()

    def _inputs(self, pages, frontmatter="---\nllm_ocr: false\n---\n", body=None):
        with self.fitz.open() as doc:
            for text in pages:
                page = doc.new_page()
                if text:
                    page.insert_text((50, 50), text)
            doc.save(self.pdf)
        self.pdf.with_suffix(".faudit.json").write_text(json.dumps({
            "pages": {str(i): {} for i in range(1, len(pages) + 1)}}))
        raw = frontmatter + ("\n".join(pages) if body is None else body)
        self.source.write_bytes(raw.encode("utf-8"))
        return raw.encode("utf-8")

    def _prepare(self):
        return runner.prepare_headings(Namespace(pdf=str(self.pdf), source=str(self.source)))

    def _assert_page_content(self, pages):
        from pdfx.scan_math import parse_md_pages
        lines, mode = parse_md_pages(self.source)
        self.assertEqual(mode, "explicit")
        for number, expected in enumerate(pages, 1):
            got = "\n".join(text for _, text, page in lines
                            if page == number and not text.startswith("<!-- PDF_PAGE:"))
            self.assertEqual("".join(got.split()), "".join(expected.split()))
        path = RUNNER.parents[1] / "llm-ocr-refresh" / "ocr_refresh_jobs.py"
        spec = importlib.util.spec_from_file_location("refresh_boundary_test", path)
        refresh = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(refresh)
        _, body = refresh.split_source_lines(self.source.read_text())
        mode, sections = refresh.page_sections(body)
        self.assertEqual(mode, "kakomon")
        self.assertEqual([key for _, key, _ in sections], list(range(1, len(pages) + 1)))
        for index, expected in enumerate(pages):
            start, end = refresh.section_range(sections, index, len(body))
            self.assertEqual("".join("".join(body[start:end]).split()), "".join(expected.split()))

    def test_recover_actual_pages_not_digit_runs(self):
        for pages in (["7\nChapter A\n100\n101\nfirst content", "9\nChapter B\nsecond content"],
                      ["Chapter A\nfirst content\n12345\nextra footer A",
                       "Chapter B\nsecond content\n12346\nextra footer B"]):
            with self.subTest(pages=pages):
                if self.pdf.exists():
                    self.pdf.unlink()
                original = self._inputs(pages)
                result = self._prepare()
                self.assertEqual(result["status"], "prepared")
                self.assertEqual(result["headings_added"], 2)
                self._assert_page_content(pages)
                updated = self.source.read_bytes()
                for n in (1, 2):
                    updated = updated.replace(f"<!-- PDF_PAGE: {n} -->\n\n".encode(), b"")
                self.assertEqual(updated, original)

    def test_preserve_coordinates_bytes_and_whitespace(self):
        for metadata in ('PDF物理页码: "44-45"\r\n', ''):
            with self.subTest(metadata=metadata):
                if self.pdf.exists():
                    self.pdf.unlink()
                pages = ["31\nAlpha beta", "32\nGamma delta"]
                fm = '---\r\n' + metadata + 'title: "中文"\r\n---\r\n'
                original = self._inputs(pages, fm, "31\r\nAlpha  beta\r\n32\r\nGamma\tdelta")
                self.assertEqual(self._prepare()["status"], "prepared")
                self._assert_page_content(pages)
                updated = self.source.read_bytes()
                self.assertNotIn("### 原书".encode(), updated)
                for n in (1, 2):
                    updated = updated.replace(f"<!-- PDF_PAGE: {n} -->\n\n".encode(), b"")
                self.assertEqual(updated, original)

    def test_existing_boundaries_are_idempotent(self):
        for body, mode in [("<!-- PDF_PAGE: 1 -->\nfirst\n<!-- PDF_PAGE: 2 -->\nsecond", "explicit"),
                           ("### 原书 p.44\nfirst\n### 原书 p.45\nsecond", "ordinal")]:
            with self.subTest(mode=mode):
                if self.pdf.exists():
                    self.pdf.unlink()
                original = self._inputs(["first", "second"], body=body)
                result = self._prepare()
                self.assertEqual((result["status"], result["boundary_mode"]), ("already_prepared", mode))
                self.assertEqual(self.source.read_bytes(), original)

    def test_metadata_marker_example_is_not_a_boundary(self):
        self._inputs(["first", "second"], '---\npage_marker: "<!-- PDF_PAGE: N -->"\n---\n')
        self.assertEqual(self._prepare()["status"], "prepared")
        self._assert_page_content(["first", "second"])

    def test_incomplete_boundaries_are_not_prepared(self):
        for body in ("<!-- PDF_PAGE: 1 -->\nfirst\nsecond",
                     "<!-- PDF_PAGE: 1 -->\nfirst\n<!-- PDF_PAGE: 1 -->\nsecond",
                     "### 原书 p.44\nfirst\nsecond"):
            with self.subTest(body=body):
                if self.pdf.exists():
                    self.pdf.unlink()
                original = self._inputs(["first", "second"], body=body)
                self.assertEqual(self._prepare()["status"], "needs_boundary_review")
                self.assertEqual(self.source.read_bytes(), original)

    def test_edited_page_is_reported_without_overwriting(self):
        original = self._inputs(["x = 2", "second intact page"], body="x = 3\nsecond intact page")
        result = self._prepare()
        self.assertEqual(result["status"], "needs_boundary_review")
        self.assertEqual(result["pages"], [1])
        self.assertEqual(self.source.read_bytes(), original)

    def test_no_text_layer_is_reported_without_overwriting(self):
        original = self._inputs(["", "second intact page"], body="OCR first page\nsecond intact page")
        result = self._prepare()
        self.assertEqual((result["status"], result["pages"]), ("needs_boundary_review", [1]))
        self.assertEqual(self.source.read_bytes(), original)

    def test_pdf_audit_count_mismatch_preserves_source(self):
        original = self._inputs(["first", "second"])
        self.pdf.with_suffix(".faudit.json").write_text(json.dumps({"pages": {"1": {}}}))
        self.assertEqual(self._prepare()["status"], "needs_boundary_review")
        self.assertEqual(self.source.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
