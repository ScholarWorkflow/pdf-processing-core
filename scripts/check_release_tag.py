"""Validate a GitHub Release tag against scholar-workflow-pdfx metadata."""

from __future__ import annotations

import argparse
import tomllib
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
RELEASE_PROJECT = Path("packaging/scholar-workflow-pdfx/pyproject.toml")


def read_release_version(repository_root: Path = REPOSITORY_ROOT) -> str:
    """Return the version declared by the independent release project."""

    project_path = repository_root / RELEASE_PROJECT
    with project_path.open("rb") as project_file:
        return tomllib.load(project_file)["project"]["version"]


def validate_release_tag(tag: str, version: str) -> None:
    """Fail closed unless *tag* is exactly ``v<version>``."""

    expected = f"v{version}"
    if tag != expected:
        raise ValueError(f"release tag {tag} does not match {expected}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", help="GitHub Release tag name")
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=REPOSITORY_ROOT,
        help="repository containing release packaging metadata",
    )
    args = parser.parse_args(argv)
    version = read_release_version(args.repository_root.resolve())
    try:
        validate_release_tag(args.tag, version)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
