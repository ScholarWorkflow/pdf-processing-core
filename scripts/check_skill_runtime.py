"""Verify skill-owned ``pdfx`` consumers use the installed distribution."""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import subprocess
import sys
from pathlib import Path


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _audit_help_command() -> list[str]:
    return [sys.executable, "-m", "pdfx.cli", "formula-audit", "--help"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, required=True)
    args = parser.parse_args(argv)
    root = args.repository_root.resolve()
    repository_lib = root / "lib"

    pdfx = importlib.import_module("pdfx")
    pdfx_path = Path(pdfx.__file__).resolve()
    if repository_lib in pdfx_path.parents:
        raise SystemExit(f"pdfx imported from checkout: {pdfx_path}")

    status = importlib.import_module("pdfx.status")
    status_path = Path(status.__file__).resolve()
    if repository_lib in status_path.parents:
        raise SystemExit(f"StatusReporter was loaded from checkout: {status_path}")

    refresh = _load_module(
        "ocr_refresh_jobs_runtime_check",
        root / ".apm/skills/llm-ocr-refresh/ocr_refresh_jobs.py",
    )

    formula = _load_module(
        "formula_repair_runner_runtime_check",
        root / ".apm/skills/formula-repair/formula_repair_runner.py",
    )
    command = formula._audit_command(
        Path("/tmp/runtime-check.pdf"),
        Path("/tmp/runtime-check.md"),
        False,
    )
    expected_prefix = [sys.executable, "-m", "pdfx.cli", "formula-audit"]
    if command[:4] != expected_prefix:
        raise SystemExit(f"unexpected audit command: {command!r}")
    if any("lib/pdfx" in item for item in command):
        raise SystemExit(f"audit command depends on checkout: {command!r}")
    result = subprocess.run(_audit_help_command(), capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(result.stdout + result.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
