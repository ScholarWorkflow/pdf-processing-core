from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pdfx import cli
from pdfx import formula_audit
from pdfx import texlayer_audit


def states(text: str) -> list[str]:
    return [
        line for line in text.splitlines()
        if line.startswith(("PROGRESS ", "RESULT ", "ERROR "))
    ]


def payload(line: str) -> dict:
    prefix = "RESULT " if line.startswith("RESULT ") else "ERROR "
    return json.loads(line[len(prefix):])


class ProgressCliTest(unittest.TestCase):
    def _args(self, **updates):
        values = {
            "pdf": "/tmp/t4-fixture.pdf",
            "extraction_dir": None,
            "dpi": 150,
            "force": False,
            "layout": False,
            "project": False,
        }
        values.update(updates)
        return Namespace(**values)

    def test_formula_audit_success_cache_and_verdict_do_not_become_failed(self):
        report = {
            "pages": {"1": {"page_verdict": "suspect", "verdicts": []}},
            "skipped": {"2": "layout_unavailable"},
            "summary": {
                "pages_audited": 2,
                "regions": 1,
                "sections": 0,
                "verdict_counts": {"suspect": 1},
            },
        }

        def fake_audit(*args, **kwargs):
            callback = kwargs["progress_event"]
            callback({"phase": "audit", "done": 0, "total": 2, "failed": 0})
            callback({"phase": "audit", "done": 2, "total": 2, "failed": 0})
            return report

        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(formula_audit, "audit_pdf", side_effect=fake_audit):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                cli.cmd_formula_audit(self._args())

        self.assertIn("pages", json.loads(stdout.getvalue()))
        result = payload(states(stderr.getvalue())[-1])
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["audit_verdict"], "suspect")
        self.assertEqual(result["skipped_pages"], 1)
        self.assertNotIn("pages", result)

        def fake_cache(*args, **kwargs):
            kwargs["progress_event"]({
                "phase": "audit", "done": 2, "total": 2,
                "failed": 0, "cache_hit": True,
            })
            return {"pages": {}, "skipped": {}, "summary": {}}

        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(formula_audit, "audit_pdf", side_effect=fake_cache):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                cli.cmd_formula_audit(self._args())
        self.assertTrue(payload(states(stderr.getvalue())[-1])["cache_hit"])

    def test_formula_audit_exception_has_traceback_before_error_state(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(
            formula_audit, "audit_pdf", side_effect=RuntimeError("fixture audit failed")
        ):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as caught:
                    cli.cmd_formula_audit(self._args())
        self.assertEqual(caught.exception.code, 1)
        error_states = states(stderr.getvalue())
        self.assertTrue(error_states[-1].startswith("ERROR "))
        self.assertEqual(payload(error_states[-1])["status"], "error")

    def test_texlayer_sampled_single_pdf_counts_actual_pages(self):
        report = {
            "mode": "sampled",
            "total_pages": 4,
            "audited_pages": 2,
            "unread_inherited_trusted": 2,
            "counts": {"trusted": 2, "suspect": 0, "n/a": 0},
            "corruption_rate": 0.0,
            "sampling": {"gate": "digital_native"},
            "arbitrated_count": 0,
            "formula_region_total": 0,
            "soft_gate_exceeded": False,
            "sections": [],
        }

        def fake_audit(*args, **kwargs):
            callback = kwargs["progress_event"]
            callback({"phase": "audit", "done": 0, "total": 4, "failed": 0})
            callback({"phase": "audit", "done": 2, "total": 4, "failed": 0})
            return report

        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(texlayer_audit, "audit_pdf", side_effect=fake_audit):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                cli.cmd_audit_texlayer(self._args(
                    no_sampling=False,
                    sample_size=None,
                    full=False,
                    detect_only=False,
                    repair_plan=False,
                    batch_archive=None,
                    library_root=None,
                    workers=4,
                ))
        result = payload(states(stderr.getvalue())[-1])
        self.assertEqual((result["done"], result["total"], result["failed"]), (2, 4, 0))
        self.assertEqual(result["unread_inherited_trusted"], 2)

    def test_texlayer_batch_continues_after_one_failure_and_is_partial(self):
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "archive"
            first = archive / "01-first"
            second = archive / "02-second"
            skipped = archive / "03-skipped"
            first.mkdir(parents=True)
            second.mkdir()
            skipped.mkdir()
            (first / "first.pdf").write_bytes(b"fixture")
            (second / "second.pdf").write_bytes(b"fixture")

            calls = []

            def fake_audit(*args, **kwargs):
                calls.append(args[0])
                if len(calls) == 1:
                    raise RuntimeError("fixture book failed")
                return {
                    "mode": "full",
                    "corruption_rate": 0.0,
                    "counts": {"trusted": 1},
                    "sections": [],
                }

            args = self._args(
                batch_archive=str(archive),
                library_root=None,
                no_sampling=False,
                sample_size=None,
                full=False,
                detect_only=False,
                repair_plan=False,
                workers=4,
            )
            stdout = io.StringIO()
            stderr = io.StringIO()
            with mock.patch.object(texlayer_audit, "audit_pdf", side_effect=fake_audit):
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    cli.cmd_audit_texlayer(args)

            batch = json.loads(stdout.getvalue())["batch"]
            self.assertEqual(len(calls), 2)
            self.assertEqual(len(batch), 2)
            result = payload(states(stderr.getvalue())[-1])
            self.assertEqual(result["status"], "partial")
            self.assertEqual(
                (result["done"], result["total"], result["failed"], result["skipped"]),
                (1, 3, 1, 1),
            )


if __name__ == "__main__":
    unittest.main()
