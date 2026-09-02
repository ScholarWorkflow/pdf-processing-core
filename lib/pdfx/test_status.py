from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pdfx.status import StatusReporter


STATUS_SCRIPT = Path(__file__).with_name("read_status.py")


class FlushBuffer(io.StringIO):
    def __init__(self):
        super().__init__()
        self.flush_count = 0

    def flush(self):
        self.flush_count += 1
        super().flush()


class StatusReporterTest(unittest.TestCase):
    def test_progress_terminal_fields_monotonic_elapsed_and_flush(self):
        ticks = iter((100.0, 100.9, 102.4))
        stream = FlushBuffer()
        reporter = StatusReporter(
            "fixture-command", stream=stream, clock=lambda: next(ticks)
        )

        reporter.progress("extract", 1, 3, 0)
        reporter.result(
            "partial", done=2, total=3, failed=1,
            phases={"extract": {"done": 2, "total": 3, "failed": 1}},
        )

        lines = stream.getvalue().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(
            lines[0],
            "PROGRESS phase=extract done=1 total=3 failed=0 elapsed_s=0",
        )
        self.assertTrue(lines[1].startswith("RESULT "))
        payload = json.loads(lines[1][len("RESULT "):])
        self.assertEqual(payload["elapsed_s"], 2)
        self.assertEqual(payload["failed"], 1)
        self.assertEqual(reporter.counts_for("extract"), {
            "done": 1, "total": 3, "failed": 0,
        })
        self.assertEqual(stream.flush_count, 2)
        self.assertNotIn("\n", lines[0])

    def test_read_status_ignores_detail_and_full_json(self):
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "run.log"
            log.write_text(
                "ordinary detail\n"
                '{"pages": [1, 2, 3]}\n'
                "PROGRESS phase=extract done=1 total=3 failed=0 elapsed_s=2\n"
                "Traceback (most recent call last):\n"
                "RESULT {\"status\":\"bad\"}\n"
                'RESULT {"status":"ok","phase":"complete","done":2,"total":3,"failed":0,"elapsed_s":8}\n'
                'ERROR {"status":"error","command":"fixture","phase":"extract","done":2,"total":3,"failed":1,"elapsed_s":9,"code":"boom","message":"short"}\n',
                encoding="utf-8",
            )
            completed = subprocess.run(
                [sys.executable, str(STATUS_SCRIPT), str(log)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0)
            self.assertEqual(
                completed.stdout.strip(),
                'ERROR {"status":"error","command":"fixture","phase":"extract","done":2,"total":3,"failed":1,"elapsed_s":9,"code":"boom","message":"short"}',
            )
            self.assertEqual(completed.stderr, "")

    def test_read_status_returns_nonzero_without_a_state(self):
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "empty.log"
            log.write_text("detail only\n{\"large\": true}\n", encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(STATUS_SCRIPT), str(log)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertEqual(completed.stdout, "")
            self.assertIn("no valid status", completed.stderr)


if __name__ == "__main__":
    unittest.main()
