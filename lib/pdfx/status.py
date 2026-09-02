"""Compact stderr status lines for long-running pdfx commands."""

from __future__ import annotations

import json
import sys
import time
from typing import Any, Callable, TextIO


def _count(value: int) -> int:
    """Normalize counters without allowing malformed negative values."""
    return max(0, int(value))


def _phase(value: str) -> str:
    """Keep the progress line's phase as one shell-friendly token."""
    return "_".join(str(value).split()) or "unknown"


class StatusReporter:
    """Write one-line progress and terminal states to a text stream.

    The default stream is stderr and the elapsed time is based only on the
    injected monotonic clock.  The optional clock makes the format testable
    without changing production behavior.
    """

    def __init__(
        self,
        command: str,
        stream: TextIO | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.command = str(command)
        self.stream = stream if stream is not None else sys.stderr
        self._clock = clock if clock is not None else time.monotonic
        self._started = self._clock()
        self._last: dict[str, Any] | None = None
        self._phase_counts: dict[str, dict[str, int]] = {}

    def elapsed_s(self) -> int:
        """Return non-negative elapsed whole seconds from the monotonic clock."""
        return max(0, int(self._clock() - self._started))

    @property
    def last_counts(self) -> dict[str, Any]:
        """Return the most recent status counters for exception reporting."""
        if self._last is None:
            return {"phase": "unknown", "done": 0, "total": 0, "failed": 0}
        return dict(self._last)

    @property
    def phase_counts(self) -> dict[str, dict[str, int]]:
        """Return the latest counters for every phase seen so far."""
        return {phase: dict(values) for phase, values in self._phase_counts.items()}

    def counts_for(self, phase: str) -> dict[str, int]:
        """Return counters for one phase, or an all-zero record if unseen."""
        return dict(self._phase_counts.get(_phase(phase), {
            "done": 0,
            "total": 0,
            "failed": 0,
        }))

    def progress(self, phase: str, done: int, total: int, failed: int = 0) -> str:
        """Write a fixed-key progress line and return the emitted line."""
        phase_token = _phase(phase)
        values = {
            "phase": phase_token,
            "done": _count(done),
            "total": _count(total),
            "failed": _count(failed),
            "elapsed_s": self.elapsed_s(),
        }
        line = (
            f"PROGRESS phase={values['phase']} done={values['done']} "
            f"total={values['total']} failed={values['failed']} "
            f"elapsed_s={values['elapsed_s']}"
        )
        self._last = dict(values)
        self._phase_counts[phase_token] = {
            "done": values["done"],
            "total": values["total"],
            "failed": values["failed"],
        }
        self._write(line)
        return line

    def result(
        self,
        status: str,
        phase: str = "complete",
        done: int = 0,
        total: int = 0,
        failed: int = 0,
        **details: Any,
    ) -> str:
        """Write a compact successful or partial terminal result."""
        payload = self._terminal_payload(
            status=status,
            phase=phase,
            done=done,
            total=total,
            failed=failed,
        )
        self._add_details(payload, details)
        line = "RESULT " + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        self._last = dict(payload)
        self._write(line)
        return line

    def error(
        self,
        phase: str = "unknown",
        done: int = 0,
        total: int = 0,
        failed: int = 1,
        code: str = "error",
        message: str = "",
        **details: Any,
    ) -> str:
        """Write a compact terminal error with a short, JSON-safe reason."""
        payload = self._terminal_payload(
            status="error",
            phase=phase,
            done=done,
            total=total,
            failed=failed,
        )
        payload.update({
            "command": self.command,
            "code": str(code),
            "message": str(message),
        })
        self._add_details(payload, details)
        line = "ERROR " + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        self._last = dict(payload)
        self._write(line)
        return line

    def _terminal_payload(
        self,
        *,
        status: str,
        phase: str,
        done: int,
        total: int,
        failed: int,
    ) -> dict[str, Any]:
        return {
            "status": str(status),
            "phase": _phase(phase),
            "done": _count(done),
            "total": _count(total),
            "failed": _count(failed),
            "elapsed_s": self.elapsed_s(),
        }

    @staticmethod
    def _add_details(payload: dict[str, Any], details: dict[str, Any]) -> None:
        required = {"status", "phase", "done", "total", "failed", "elapsed_s"}
        for key, value in details.items():
            if key not in required:
                payload[str(key)] = value

    def _write(self, line: str) -> None:
        self.stream.write(line + "\n")
        self.stream.flush()
