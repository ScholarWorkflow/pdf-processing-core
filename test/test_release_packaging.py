from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STAGE_SCRIPT = ROOT / "scripts/stage_release_package.py"
TAG_SCRIPT = ROOT / "scripts/check_release_tag.py"
SPEC = importlib.util.spec_from_file_location("stage_release_package", STAGE_SCRIPT)
assert SPEC and SPEC.loader
stage_release_package = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = stage_release_package
SPEC.loader.exec_module(stage_release_package)


def _project(path: Path) -> dict:
    with path.open("rb") as project_file:
        return tomllib.load(project_file)


def _staged_files(root: Path) -> dict[Path, bytes]:
    return {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _expected_stage_files() -> dict[Path, bytes]:
    expected = {
        Path("pyproject.toml"): (
            ROOT / "packaging/scholar-workflow-pdfx/pyproject.toml"
        ).read_bytes()
    }
    for source in (ROOT / "lib/pdfx").rglob("*"):
        if source.is_file() and "__pycache__" not in source.parts and source.suffix != ".pyc":
            relative = source.relative_to(ROOT / "lib/pdfx")
            expected[Path("lib/pdfx") / relative] = source.read_bytes()
    return expected


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


def test_staging_is_complete_byte_exact_and_idempotent(tmp_path):
    destination = tmp_path / "stage"
    destination.mkdir()
    (destination / "stale.txt").write_text("stale", encoding="utf-8")
    expected = _expected_stage_files()

    first = stage_release_package.stage_release_package(destination, ROOT)
    assert first == destination.resolve()
    assert _staged_files(first) == expected

    (destination / "stale-again.txt").write_text("stale again", encoding="utf-8")
    second = stage_release_package.stage_release_package(destination, ROOT)
    assert second == destination.resolve()
    assert _staged_files(second) == expected


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


def test_release_tag_mismatch_fails_closed():
    release_version = _project(
        ROOT / "packaging/scholar-workflow-pdfx/pyproject.toml"
    )["project"]["version"]

    valid = subprocess.run(
        [sys.executable, str(TAG_SCRIPT), f"v{release_version}", "--repository-root", str(ROOT)],
        capture_output=True,
        text=True,
    )
    assert valid.returncode == 0, valid.stdout + valid.stderr

    invalid = subprocess.run(
        [sys.executable, str(TAG_SCRIPT), "v9.9.9", "--repository-root", str(ROOT)],
        capture_output=True,
        text=True,
    )
    assert invalid.returncode != 0
    assert f"does not match v{release_version}" in invalid.stderr


def test_publish_workflow_contract():
    workflow = (ROOT / ".github/workflows/publish-pypi.yml").read_text(encoding="utf-8")
    assert "\non:\n  release:\n    types: [published]\n" in workflow
    for forbidden_trigger in ("workflow_dispatch:", "pull_request:", "push:"):
        assert forbidden_trigger not in workflow

    assert "scripts/check_release_tag.py" in workflow
    assert "environment: pypi" in workflow
    for long_lived_credential in ("PYPI_TOKEN", "password:", "username:"):
        assert long_lived_credential not in workflow

    build_job, publish_job = workflow.split("\n  publish:\n", 1)
    assert "id-token: write" not in build_job
    assert "permissions:\n      id-token: write" in publish_job
    assert "actions/download-artifact@v4" in publish_job
    assert "pypa/gh-action-pypi-publish@release/v1" in publish_job
    assert "actions/checkout@" not in publish_job
    assert "uv build" not in publish_job
    assert "stage_release_package.py" not in publish_job


def test_uv_build_produces_wheel_and_sdist(tmp_path):
    destination = stage_release_package.stage_release_package(tmp_path / "stage", ROOT)
    output = tmp_path / "dist"
    version = _project(ROOT / "packaging/scholar-workflow-pdfx/pyproject.toml")["project"]["version"]

    result = subprocess.run(
        ["uv", "build", str(destination), "--out-dir", str(output)],
        cwd=tmp_path,
        env={**os.environ, "UV_NO_PROGRESS": "1"},
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert list(output.glob(f"scholar_workflow_pdfx-{version}-*.whl"))
    assert list(output.glob(f"scholar_workflow_pdfx-{version}.tar.gz"))
