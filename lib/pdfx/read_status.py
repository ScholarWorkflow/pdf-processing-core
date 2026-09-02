#!/usr/bin/env python3
"""Print only the last valid compact status line from a command log."""

from __future__ import annotations

import json
import sys
from typing import Any


_COMMON_KEYS = {"status", "phase", "done", "total", "failed", "elapsed_s"}


def _is_nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _valid_payload(payload: Any, kind: str) -> bool:
    if not isinstance(payload, dict) or not _COMMON_KEYS.issubset(payload):
        return False
    if not isinstance(payload["status"], str) or not payload["status"]:
        return False
    if not isinstance(payload["phase"], str) or not payload["phase"]:
        return False
    if not all(_is_nonnegative_int(payload[key]) for key in ("done", "total", "failed", "elapsed_s")):
        return False
    if kind == "ERROR":
        return (
            payload.get("status") == "error"
            and isinstance(payload.get("command"), str)
            and bool(payload["command"])
            and isinstance(payload.get("code"), str)
            and bool(payload["code"])
            and isinstance(payload.get("message"), str)
        )
    return True


def parse_status_line(raw_line: str) -> str | None:
    """Return a line if it is a valid PROGRESS, RESULT, or ERROR record."""
    line = raw_line.rstrip("\r\n").strip()
    if not line:
        return None

    if line.startswith("PROGRESS "):
        fields: dict[str, str] = {}
        for token in line.split()[1:]:
            if "=" not in token:
                return None
            key, value = token.split("=", 1)
            if not key or key in fields:
                return None
            fields[key] = value
        required = {"phase", "done", "total", "failed", "elapsed_s"}
        if not required.issubset(fields):
            return None
        if not fields["phase"]:
            return None
        try:
            counts = [int(fields[key]) for key in ("done", "total", "failed", "elapsed_s")]
        except ValueError:
            return None
        if any(value < 0 for value in counts):
            return None
        return line

    for kind in ("RESULT", "ERROR"):
        prefix = kind + " "
        if not line.startswith(prefix):
            continue
        try:
            payload = json.loads(line[len(prefix):])
        except json.JSONDecodeError:
            return None
        return line if _valid_payload(payload, kind) else None
    return None


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: read_status.py LOG", file=sys.stderr)
        return 2

    path = argv[0]
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            last = None
            for line in handle:
                parsed = parse_status_line(line)
                if parsed is not None:
                    last = parsed
    except OSError as exc:
        print(f"read_status: cannot read log: {exc}", file=sys.stderr)
        return 1

    if last is None:
        print("read_status: no valid status", file=sys.stderr)
        return 1
    print(last)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
