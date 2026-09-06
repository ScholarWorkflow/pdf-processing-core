"""Package-first relocation contract for the deployed skill copies.

APM installs this repo twice in a consumer: the producer project is cloned
under ``<consumer root>/apm_modules/ScholarWorkflow/pdf-processing-core/``
(``pyproject.toml``, ``uv.lock``, ``lib/pdfx``) and the skill files are
additionally deployed verbatim to ``<consumer root>/.agents/skills/<skill>/``.

This repo is a normal Python project: ``pdfx`` and its dependencies come from
the producer project environment (one ``.venv`` under the project root), and
``uv run --project ... --locked`` creates and synchronizes it lazily. The
contract pinned here is therefore **project environment + installed package**:
the relocated skill copies must execute for real under the producer project
environment, and neither runner may depend on source-tree topology
(``sys.path`` shims, ``lib/pdfx/cli.py`` path resolution) or ad hoc dependency
injection (``uv run --with pymupdf``). Existence checks alone are
insufficient: a documented route can point at an existing file that cannot
execute, so both deployed runners are exercised end-to-end.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from test_codex_compat import MODULE_SOURCE_ROUTE, ROOT, SKILLS_DIR

# runner basename -> repo-relative runner path
RUNNERS = {
    "formula_repair_runner.py": ".apm/skills/formula-repair/formula_repair_runner.py",
    "ocr_refresh_jobs.py": ".apm/skills/llm-ocr-refresh/ocr_refresh_jobs.py",
}

# Every deployed documentation surface that could carry an execution route.
DOC_SURFACES = (
    ROOT / "CODEX-COMPATIBILITY.md",
    *sorted((ROOT / ".apm" / "agents").glob("*.agent.md")),
    *sorted(SKILLS_DIR.glob("*/SKILL.md")),
)

UV_PROJECT_CONTRACT = 'uv run --project "<pdf-processing-core project root>" --locked'


def _uv_env() -> dict:
    """The contract must hold without inherited development state: probe with
    a whitelist (HOME for the uv cache, a PATH that finds `uv`) so no
    PYTHONPATH/VIRTUAL_ENV leak can fake a working environment."""
    uv_dir = Path(shutil.which("uv") or "uv").resolve().parent
    return {
        "HOME": os.environ.get("HOME", str(Path.home())),
        "PATH": f"{uv_dir}:/usr/bin:/bin:/usr/local/bin",
    }


def _uv_run(module_root: Path, *args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["uv", "run", "--project", str(module_root), "--locked", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=_uv_env(),
    )


# The driver runs inside the consumer's producer project environment: it loads
# the DEPLOYED formula runner, exercises _audit_command against a generated
# mini PDF, and requires the real `python -m pdfx.cli formula-audit` path to
# produce the audit sidecar.
FORMULA_AUDIT_DRIVER = (
    "import importlib.util, subprocess, sys",
    "from pathlib import Path",
    "import pymupdf",
    "runner_path = Path('.agents/skills/formula-repair/formula_repair_runner.py').resolve()",
    "spec = importlib.util.spec_from_file_location('deployed_formula_repair_runner', runner_path)",
    "runner = importlib.util.module_from_spec(spec)",
    "spec.loader.exec_module(runner)",
    "doc = pymupdf.open()",
    "page = doc.new_page()",
    "page.insert_text((72, 100), 'The quadratic formula gives roots of ax^2+bx+c=0.')",
    "page.insert_text((72, 140), 'Plain narrative text follows here for extraction.')",
    "pdf = Path('audit-probe.pdf').resolve()",
    "doc.save(str(pdf))",
    "doc.close()",
    "command = runner._audit_command(pdf, pdf, False)",
    "assert command[:3] == [sys.executable, '-m', 'pdfx.cli'], command",
    "assert all(not item.endswith('cli.py') for item in command), command",
    "result = subprocess.run(command, capture_output=True, text=True)",
    "assert result.returncode == 0, result.stderr",
    "assert pdf.with_suffix('.faudit.json').is_file(), 'no .faudit.json sidecar'",
    "print('ok')",
)


@pytest.fixture(scope="module")
def consumer(tmp_path_factory) -> dict:
    """A simulated clean APM consumer: producer project under apm_modules/,
    verbatim skill deployment under .agents/skills/, no repository checkout,
    no launcher, no inherited Python environment."""
    root = tmp_path_factory.mktemp("apm-consumer")
    module_root = root / MODULE_SOURCE_ROUTE
    module_root.mkdir(parents=True)
    shutil.copy2(ROOT / "pyproject.toml", module_root / "pyproject.toml")
    shutil.copy2(ROOT / "uv.lock", module_root / "uv.lock")
    shutil.copytree(ROOT / "lib", module_root / "lib")
    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        shutil.copytree(
            skill_dir,
            root / ".agents" / "skills" / skill_dir.name,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    sync = subprocess.run(
        ["uv", "sync", "--project", str(module_root), "--locked"],
        cwd=root,
        capture_output=True,
        text=True,
        env=_uv_env(),
    )
    assert sync.returncode == 0, sync.stderr
    return {"root": root, "module_root": module_root}


# --------------------------------------------------------------------------- producer project environment


def test_producer_project_environment_is_created_and_used(consumer):
    """One `.venv` under the installed producer project; `pdfx` resolves from
    it — not from the checkout, a tool install, or an overlay."""
    venv = consumer["module_root"] / ".venv"
    assert venv.is_dir(), "uv sync must create the producer project .venv"

    probe = _uv_run(
        consumer["module_root"],
        "python", "-c",
        "import pdfx, pdfx.quality, pdfx.extract, pdfx.status\n"
        "from pathlib import Path\n"
        "location = Path(pdfx.__file__).resolve()\n"
        "assert str(location).startswith(str(Path('.').resolve())), location\n"
        "print('ok')",
        cwd=consumer["module_root"],
    )
    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.strip() == "ok"

    cli = _uv_run(consumer["module_root"], "pdfx", "--help", cwd=consumer["root"])
    assert cli.returncode == 0, cli.stderr


# --------------------------------------------------------------------------- deployed runners execute


def test_deployed_ocr_runner_runs_under_project_environment(consumer):
    """The relocated `.agents/skills/llm-ocr-refresh/ocr_refresh_jobs.py` copy
    must start under the producer project environment (its `pdfx.status`
    import resolves from the installed package), with no environment leak."""
    deployed = (
        consumer["root"] / ".agents" / "skills" / "llm-ocr-refresh" / "ocr_refresh_jobs.py"
    )
    assert deployed.is_file(), "APM deploys skill files verbatim"
    result = _uv_run(consumer["module_root"], "python", str(deployed), "--help", cwd=consumer["root"])
    assert result.returncode == 0, result.stderr


def test_deployed_formula_runner_post_merge_audit_runs(consumer):
    """The relocated formula runner's post-merge audit must run the installed
    package through the running interpreter (`python -m pdfx.cli`) and really
    produce the `.faudit.json` sidecar finalize is verified against."""
    result = _uv_run(
        consumer["module_root"],
        "python", "-c", " \n".join(FORMULA_AUDIT_DRIVER),
        cwd=consumer["root"],
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


# --------------------------------------------------------------------------- pinned constraints


def test_runners_carry_no_source_topology_or_dependency_injection():
    """Package-first means exactly that: no `sys.path` shim to a source-tree
    `lib/`, no source `cli.py` resolution, no `uv run --with` overlay."""
    for repo_path in RUNNERS.values():
        text = (ROOT / repo_path).read_text(encoding="utf-8")
        assert "sys.path.insert" not in text, f"{repo_path}: sys.path topology shim"
        assert "--with pymupdf" not in text, f"{repo_path}: ad hoc dependency injection"
        assert "cli.py" not in text, f"{repo_path}: source-tree cli.py resolution"


def test_documented_routes_bind_docs_to_the_project_contract(consumer):
    """Wherever a doc surface names a runner it must express the producer uv
    project contract; `--with` injection and 'deployment is not executable'
    claims must not come back, and the APM producer-root resolution must stay
    documented."""
    for surface in DOC_SURFACES:
        text = surface.read_text(encoding="utf-8")
        assert "--with pymupdf" not in text, f"{surface.name}: ad hoc dependency injection documented"
        assert "must not be executed" not in text, (
            f"{surface.name}: the deployed skill copy is an execution route "
            "under the producer project environment"
        )
        for basename in RUNNERS:
            if basename in text:
                assert UV_PROJECT_CONTRACT in text, (
                    f"{surface.name}: references {basename} without the "
                    f"producer project contract '{UV_PROJECT_CONTRACT} ...'"
                )
                assert ".agents/skills/" in text, (
                    f"{surface.name}: does not document the deployed skill "
                    "location as an execution route"
                )

    compat = (ROOT / "CODEX-COMPATIBILITY.md").read_text(encoding="utf-8")
    assert MODULE_SOURCE_ROUTE in compat, (
        "CODEX-COMPATIBILITY.md must document the APM producer project root"
    )
    assert compat.count(MODULE_SOURCE_ROUTE) >= 1
