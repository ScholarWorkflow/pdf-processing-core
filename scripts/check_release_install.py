"""Smoke-test a built ``scholar-workflow-pdfx`` install outside the checkout."""

from __future__ import annotations

import argparse
import importlib
import os
import subprocess
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository-root",
        type=Path,
        help="checkout root whose source tree must not satisfy the imports",
    )
    args = parser.parse_args(argv)

    repository_lib = None
    if args.repository_root:
        repository_lib = (args.repository_root.resolve() / "lib").resolve()
        if any(Path(entry or os.curdir).resolve() == repository_lib for entry in sys.path):
            raise SystemExit("repository lib/ is present on sys.path")

    for module_name in ("pdfx", "pdfx.quality", "pdfx.extract", "pdfx.status"):
        module = importlib.import_module(module_name)
        module_path = Path(module.__file__).resolve()
        if repository_lib and repository_lib in module_path.parents:
            raise SystemExit(f"{module_name} imported from checkout: {module_path}")

    pdfx = os.environ.get("PDFX_EXECUTABLE", "pdfx")
    for arguments in (("--help",), ("quality", "--help")):
        result = subprocess.run([pdfx, *arguments], capture_output=True, text=True)
        if result.returncode != 0:
            raise SystemExit(result.stdout + result.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
