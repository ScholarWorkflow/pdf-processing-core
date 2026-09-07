from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/stage_release_package.py"
SPEC = importlib.util.spec_from_file_location("stage_release_package", SCRIPT)
assert SPEC and SPEC.loader
stage_release_package = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = stage_release_package
SPEC.loader.exec_module(stage_release_package)


def _project(path: Path) -> dict:
    with path.open("rb") as project_file:
        return tomllib.load(project_file)


def test_release_metadata_matches_compatibility_contract():
    root = _project(ROOT / "pyproject.toml")["project"]
    release = _project(ROOT / "packaging/scholar-workflow-pdfx/pyproject.toml")["project"]

    assert release["name"] == "scholar-workflow-pdfx"
    assert root["name"] == "pdf-processing-core"
    assert release["version"] == root["version"] == "0.1.0"
    assert release["requires-python"] == root["requires-python"] == ">=3.11"
    assert release["dependencies"] == root["dependencies"] == ["PyMuPDF>=1.24"]
    assert release["scripts"] == root["scripts"] == {"pdfx": "pdfx.cli:main"}
    assert stage_release_package._read_package_version(ROOT / "lib/pdfx") == release["version"]


def test_staging_copies_canonical_package_without_rewriting(tmp_path):
    destination = tmp_path / "stage"
    destination.mkdir()
    (destination / "stale.txt").write_text("stale", encoding="utf-8")

    staged = stage_release_package.stage_release_package(destination, ROOT)

    assert staged == destination.resolve()
    assert not (staged / "stale.txt").exists()
    assert (staged / "pyproject.toml").read_bytes() == (
        ROOT / "packaging/scholar-workflow-pdfx/pyproject.toml"
    ).read_bytes()

    source_files = sorted((ROOT / "lib/pdfx").rglob("*"))
    for source in source_files:
        if source.is_file() and "__pycache__" not in source.parts and source.suffix != ".pyc":
            relative = source.relative_to(ROOT / "lib/pdfx")
            staged_file = staged / "lib/pdfx" / relative
            assert staged_file.is_file()
            assert staged_file.read_bytes() == source.read_bytes()
    assert not list((staged / "lib/pdfx").rglob("__pycache__"))
    assert not list((staged / "lib/pdfx").rglob("*.pyc"))


def test_staging_rejects_version_drift(tmp_path):
    fake_root = tmp_path / "repo"
    (fake_root / "lib/pdfx").mkdir(parents=True)
    (fake_root / "packaging/scholar-workflow-pdfx").mkdir(parents=True)
    (fake_root / "lib/pdfx/__init__.py").write_text('__version__ = "0.1.1"\n', encoding="utf-8")
    root_project = """\
[project]
name = "pdf-processing-core"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["PyMuPDF>=1.24"]

[project.scripts]
pdfx = "pdfx.cli:main"
"""
    release_project = root_project.replace("version = \"0.1.0\"", "version = \"0.1.1\"")
    (fake_root / "pyproject.toml").write_text(root_project, encoding="utf-8")
    (fake_root / "packaging/scholar-workflow-pdfx/pyproject.toml").write_text(
        release_project, encoding="utf-8"
    )

    try:
        stage_release_package.validate_release_contract(fake_root)
    except ValueError as error:
        assert "release version mismatch" in str(error)
    else:
        raise AssertionError("version drift must fail release validation")


def test_uv_build_produces_wheel_and_sdist(tmp_path):
    destination = stage_release_package.stage_release_package(tmp_path / "stage", ROOT)
    output = tmp_path / "dist"

    result = subprocess.run(
        ["uv", "build", str(destination), "--out-dir", str(output)],
        cwd=tmp_path,
        env={**os.environ, "UV_NO_PROGRESS": "1"},
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert list(output.glob("scholar_workflow_pdfx-0.1.0-*.whl"))
    assert list(output.glob("scholar_workflow_pdfx-0.1.0.tar.gz"))
