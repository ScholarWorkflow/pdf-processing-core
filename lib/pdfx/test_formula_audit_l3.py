"""Focused no-model tests for the formula-audit L3 sidecar cache."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pdfx import formula_audit as fa


class FormulaAuditL3Test(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.pdf = Path(self.temp.name) / "section.pdf"
        self.pdf.write_bytes(b"%PDF fake")
        self.entry = {"bbox_pt": [1, 2, 3, 4], "text": "x=y", "verdict": "pending_l3", "signals": []}
        self.data = {"fingerprint": "pdf-fingerprint", "pages": {"1": {"page_verdict": "pending_l3", "verdicts": [self.entry]}},
                     "report": {"sections": [{"pages": [1]}]}, "summary": {}}
        fa.faudit_path(str(self.pdf)).write_text(json.dumps(self.data), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_low_risk_plan_is_local(self):
        plan = fa.pending_l3_plan(str(self.pdf), "trusted")
        self.assertEqual(len(plan["low_risk"]), 1)
        self.assertEqual(plan["sample"], [])

    def test_apply_l3_check_updates_existing_sidecar(self):
        fingerprint = fa.region_fingerprint("pdf-fingerprint", 1, self.entry)
        result = fa.apply_l3_checks(str(self.pdf), [{"page": 1, "region_fingerprint": fingerprint,
                                                     "status": "passed", "method": "low-risk-rule"}])
        self.assertEqual(result, {"applied": 1, "stale": 0})
        saved = fa.load_faudit(str(self.pdf))
        self.assertEqual(saved["pages"]["1"]["verdicts"][0]["verdict"], "ok")
        self.assertEqual(saved["pages"]["1"]["verdicts"][0]["l3_check"]["method"], "low-risk-rule")

    def test_high_risk_sample_passes_without_repair(self):
        long_formula = "f(x) = \\sum_{i=1}^{n} a_i x^{i} + \\sqrt{b}/2" + "x" * 20
        entry2 = {"bbox_pt": [5, 6, 7, 8], "text": long_formula, "verdict": "pending_l3", "signals": []}
        self.data["pages"]["1"]["verdicts"].append(entry2)
        fa.faudit_path(str(self.pdf)).write_text(json.dumps(self.data), encoding="utf-8")
        plan = fa.pending_l3_plan(str(self.pdf), "trusted")
        self.assertEqual(plan["eligible"], True)
        self.assertEqual([i["bbox_pt"] for i in plan["low_risk"]], [[1, 2, 3, 4]])
        # stable fingerprint order, at most 3 samples
        self.assertEqual(len(plan["sample"]), 1)
        self.assertEqual(plan["sample"][0]["bbox_pt"], [5, 6, 7, 8])
        result = fa.apply_l3_checks(str(self.pdf), [
            {"page": 1, "region_fingerprint": item["region_fingerprint"], "status": "passed",
             "method": "risk-sample"} for item in plan["sample"]])
        self.assertEqual(result["applied"], 1)
        saved = fa.load_faudit(str(self.pdf))
        verdicts = {tuple(v["bbox_pt"]): v["verdict"] for v in saved["pages"]["1"]["verdicts"]}
        self.assertEqual(verdicts[(1, 2, 3, 4)], "pending_l3")   # low-risk untouched until applied
        self.assertEqual(verdicts[(5, 6, 7, 8)], "ok")           # consistent sample passes

    def test_one_inconsistency_escalates_all_high_risk(self):
        entries = [{"bbox_pt": [i, 0, i + 1, 5],
                    "text": "x^" + str(i) + "/2 + \\sqrt{y}" * 3, "verdict": "pending_l3", "signals": []}
                   for i in range(5)]
        self.data["pages"]["1"]["verdicts"] = entries
        fa.faudit_path(str(self.pdf)).write_text(json.dumps(self.data), encoding="utf-8")
        plan = fa.pending_l3_plan(str(self.pdf), "trusted")
        self.assertEqual(len(plan["sample"]), 3)
        self.assertEqual(len(plan["high_risk"]), 5)
        # rule: ONE inconsistent sample escalates the inconsistent region AND
        # every remaining high-risk region of the same PDF
        checks = [{"page": 1, "region_fingerprint": item["region_fingerprint"],
                   "status": "escalated", "method": "risk-sample"} for item in plan["high_risk"]]
        result = fa.apply_l3_checks(str(self.pdf), checks)
        self.assertEqual(result["applied"], 5)
        saved = fa.load_faudit(str(self.pdf))
        self.assertTrue(all(v["verdict"] == "suspect" for v in saved["pages"]["1"]["verdicts"]))
        self.assertEqual(saved["pages"]["1"]["page_verdict"], "suspect")

    def test_force_audit_preserves_matching_l3_cache_and_drops_stale(self):
        import pymupdf
        from pdfx import formula_regions as fr
        doc = pymupdf.open()
        doc.new_page(width=300, height=160)
        doc.save(str(self.pdf))
        doc.close()
        fp = fr.fingerprint(str(self.pdf), 150)
        region_a = {"bbox_pt": [10, 10, 100, 30], "text": "x = y + 1",
                    "class": "display_formula", "source": "span"}
        region_b = {"bbox_pt": [10, 60, 200, 90], "text": "E = m c^{2} + \\sqrt{2}",
                    "class": "display_formula", "source": "span"}
        fr.write_sidecar(str(self.pdf), {
            "fingerprint": fp, "dpi": 150, "generated_at": "t", "known_limits": [],
            "pages": {"1": [region_a, region_b]}, "skipped": {}, "summary": {}})
        fresh_entry = dict(region_a, verdict="pending_l3", signals=[])
        cached = fa.region_fingerprint(fp, 1, fresh_entry)
        self.data = {"fingerprint": fp, "pages": {"1": {"page_verdict": "pending_l3", "verdicts": [
                        dict(fresh_entry, l3_check={"status": "passed", "method": "risk-sample",
                                                    "rule_version": 1, "region_fingerprint": cached})]}},
                     "report": {"sections": []}, "summary": {}}
        fa.faudit_path(str(self.pdf)).write_text(json.dumps(self.data), encoding="utf-8")
        result = fa.audit_pdf(str(self.pdf), force=True)
        verdicts = result["pages"]["1"]["verdicts"]
        by_bbox = {tuple(v["bbox_pt"]): v for v in verdicts}
        kept = by_bbox[(10, 10, 100, 30)]
        self.assertEqual(kept["verdict"], "ok")
        self.assertEqual(kept["l3_check"]["status"], "passed")
        self.assertEqual(by_bbox[(10, 60, 200, 90)]["verdict"], "suspect")  # fresh signals, no cache -> never promoted

    def test_llm_ocr_accepts_trusted_span_regions(self):
        region = {"bbox_pt": [1, 2, 30, 20], "text": "e^x^2 + 中文）",
                  "class": "display_formula", "source": "span"}
        repaired = fa._judge_region(None, region, "trusted", False, llm_ocr_ok=True)
        self.assertEqual(repaired, {"verdict": "ok", "signals": ["llm_ocr_repaired"]})

        # Without the repair-complete marker, the same layer signal remains
        # suspect; the acceptance path must not weaken the normal audit.
        unrepaired = fa._judge_region(None, region, "trusted", False, llm_ocr_ok=False)
        self.assertEqual(unrepaired["verdict"], "suspect")


if __name__ == "__main__":
    unittest.main()
