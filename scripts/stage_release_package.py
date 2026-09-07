"""Stage the canonical ``pdfx`` package for an independent release build."""

from __future__ import annotations

import argparse
import ast
import shutil
import tomllib
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
RELEASE_PROJECT = Path("packaging/scholar-workflow-pdfx/pyproject.toml")
CANONICAL_PACKAGE = Path("lib/pdfx")


def _ignore_generated_files(_directory: str, names: list[str]) -> set[str]:
    return {
        name
        for name in names
        if name == "__pycache__" or name.endswith(".pyc")
    }


def _read_project(path: Path) -> dict:
    with path.open("rb") as project_file:
        return tomllib.load(project_file)


def _read_package_version(package_root: Path) -> str:
    module_path = package_root / "__init__.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__version__":
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        return node.value.value
    raise ValueError(f"{module_path} does not define a string __version__")


def validate_release_contract(repository_root: Path = REPOSITORY_ROOT) -> dict:
    """Validate and return the release metadata shared by the source tree."""

    root_project_path = repository_root / "pyproject.toml"
    release_project_path = repository_root / RELEASE_PROJECT
    package_root = repository_root / CANONICAL_PACKAGE
    root_project = _read_project(root_project_path)
    release_project = _read_project(release_project_path)

    root_metadata = root_project["project"]
    release_metadata = release_project["project"]
    versions = {
        "root project": root_metadata["version"],
        "release project": release_metadata["version"],
        "pdfx package": _read_package_version(package_root),
    }
    if len(set(versions.values())) != 1:
        details = ", ".join(f"{name}={version}" for name, version in versions.items())
        raise ValueError(f"release version mismatch: {details}")

    if release_metadata["requires-python"] != root_metadata["requires-python"]:
        raise ValueError("release requires-python does not match the root compatibility project")
    if release_metadata["dependencies"] != root_metadata["dependencies"]:
        raise ValueError("release dependencies do not match the root compatibility project")
    if release_project["project"]["scripts"] != root_project["project"]["scripts"]:
        raise ValueError("release console entry points do not match the root compatibility project")

    if not package_root.is_dir():
        raise FileNotFoundError(f"canonical package directory is missing: {package_root}")

    return {
        "version": versions["release project"],
        "release_project": release_project_path,
        "canonical_package": package_root,
    }


def stage_release_package(destination: Path, repository_root: Path = REPOSITORY_ROOT) -> Path:
    """Create a clean release project containing the canonical ``lib/pdfx`` tree."""

    contract = validate_release_contract(repository_root)
    repository_root = repository_root.resolve()
    destination = Path(destination).resolve()
    if destination == repository_root or repository_root in destination.parents:
        raise ValueError("staging destination must be outside the repository")

    if destination.is_symlink() or destination.is_file():
        destination.unlink()
    elif destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)

    shutil.copy2(contract["release_project"], destination / "pyproject.toml")
    shutil.copytree(
        contract["canonical_package"],
        destination / CANONICAL_PACKAGE,
        copy_function=shutil.copy2,
        ignore=_ignore_generated_files,
    )
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=REPOSITORY_ROOT,
        help="repository containing the canonical source tree",
    )
    args = parser.parse_args(argv)
    staged = stage_release_package(args.destination, args.repository_root.resolve())
    print(staged)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
