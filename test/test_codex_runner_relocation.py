"""Relocation contract for the no-`skillrepo` runner route (Codex consumers).

APM installs this repo twice in a consumer: the module source is cloned under
``<consumer root>/apm_modules/ScholarWorkflow/pdf-processing-core/`` and the
skill files are additionally deployed verbatim to
``<consumer root>/.agents/skills/<skill>/``. The skill runners resolve this
repo's own ``lib/pdfx`` tooling relative to their own location, so only the
module-source copy keeps the repository topology they are written against.
From the relocated deployment, ``formula_repair_runner.py`` would resolve its
post-merge audit CLI to ``<consumer root>/lib/pdfx/cli.py`` (absent in a clean
consumer) and ``ocr_refresh_jobs.py`` cannot import ``pdfx.status`` at all.

The agent bodies, skill documents, and CODEX-COMPATIBILITY.md therefore name
the installed module source as the ONLY no-launcher execution route. These
tests pin that documented route against a simulated clean-consumer layout and
exercise both runners through it for real — the formula runner's audit
command is executed end-to-end against a generated PDF, and the OCR runner
starts through its documented ``uv run --with pymupdf,pillow`` command form.
Existence checks alone are insufficient: a documented route can point at an
existing file that cannot execute.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from test_codex_compat import AGENTS_DIR, MODULE_SOURCE_ROUTE, ROOT, SKILLS_DIR

# runner basename -> repo-relative module-source path
RUNNERS = {
    "formula_repair_runner.py": ".apm/skills/formula-repair/formula_repair_runner.py",
    "ocr_refresh_jobs.py": ".apm/skills/llm-ocr-refresh/ocr_refresh_jobs.py",
}

# Every deployed documentation surface that could carry an execution route.
DOC_SURFACES = tuple(sorted(AGENTS_DIR.glob("*.agent.md"))) + (
    ROOT / "CODEX-COMPATIBILITY.md",
    *sorted(SKILLS_DIR.glob("*/SKILL.md")),
)

UV_RUN = ["uv", "run", "--with", "pymupdf,pillow", "python3"]


def _isolated_env() -> dict:
    """The documented command must resolve `pdfx` through the module source,
    so the probe must not inherit a development environment that happens to
    expose a repository checkout (PYTHONPATH, an activated VIRTUAL_ENV with
    the project installed, ...). A clean consumer has no such leak, so probe
    with a whitelist: HOME for the uv cache and a PATH that finds `uv`."""
    uv_dir = Path(shutil.which("uv") or "uv").resolve().parent
    return {
        "HOME": os.environ.get("HOME", str(Path.home())),
        "PATH": f"{uv_dir}:/usr/bin:/bin:/usr/local/bin",
    }

PROBE_PDF_SCRIPT = (
    "import pymupdf\n"
    "doc = pymupdf.open()\n"
    "page = doc.new_page()\n"
    "page.insert_text((72, 100), 'The quadratic formula gives roots of ax^2+bx+c=0.')\n"
    "page.insert_text((72, 140), 'Plain narrative text follows here for extraction.')\n"
    "doc.save('audit-probe.pdf')\n"
)


def _load_module(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def consumer(tmp_path_factory) -> dict:
    """A simulated clean APM consumer: module source under apm_modules/, the
    verbatim skill deployment under .agents/skills/, no repository checkout,
    no `skillrepo` launcher, and a generated probe PDF to run against."""
    root = tmp_path_factory.mktemp("apm-consumer")
    module_src = root / MODULE_SOURCE_ROUTE
    for part in (".apm", "lib"):
        shutil.copytree(ROOT / part, module_src / part)
    shutil.copy2(ROOT / "pyproject.toml", module_src / "pyproject.toml")
    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        shutil.copytree(skill_dir, root / ".agents" / "skills" / skill_dir.name)
    probe = subprocess.run(
        ["uv", "run", "--with", "pymupdf", "python3", "-c", PROBE_PDF_SCRIPT],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert probe.returncode == 0, probe.stderr
    assert (root / "audit-probe.pdf").is_file()
    return {"root": root, "module_src": module_src}


# --------------------------------------------------------------------------- documented route


def test_documented_no_launcher_route_names_module_source(consumer):
    """Wherever a doc surface names a runner for the no-launcher route, the
    named location must be the installed module source; the relocated
    `.agents/skills/` deployment must never be presented as an execution
    route."""
    for surface in DOC_SURFACES:
        text = surface.read_text(encoding="utf-8")
        assert not re.search(r"<consumer root>/\.agents/skills/", text), (
            f"{surface.name}: documents the relocated .agents/skills/ copy as "
            "an execution route; the runners only work from the module source"
        )
        for basename, repo_path in RUNNERS.items():
            if basename not in text:
                continue
            route = f"<consumer root>/{MODULE_SOURCE_ROUTE}{repo_path}"
            assert route in text, (
                f"{surface.name}: references {basename} without the installed "
                f"module-source route {route}"
            )
            assert (consumer["module_src"] / repo_path).is_file()

    compat = (ROOT / "CODEX-COMPATIBILITY.md").read_text(encoding="utf-8")
    assert MODULE_SOURCE_ROUTE in compat, (
        "CODEX-COMPATIBILITY.md must document the module-source runner route"
    )


# --------------------------------------------------------------------------- runtime through the documented route


def test_formula_runner_audit_command_resolves_and_runs(consumer):
    """The module-source runner's post-merge audit resolution must point back
    into the module source, and the assembled command must actually execute
    against a real PDF from the consumer root (no repo checkout, no launcher)."""
    runner = _load_module(
        consumer["module_src"] / RUNNERS["formula_repair_runner.py"]
    )
    pdf = consumer["root"] / "audit-probe.pdf"
    command = runner._audit_command(pdf, pdf, textbook=False)
    cli = Path(command[5])
    assert cli == consumer["module_src"] / "lib" / "pdfx" / "cli.py"
    assert cli.is_file()
    # re-wrap the documented uv prefix so the probe does not nest uv environments
    result = subprocess.run(
        ["uv", "run", "--with", "pymupdf", "python3", *command[5:]],
        cwd=consumer["root"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_ocr_runner_imports_via_documented_command(consumer):
    """`ocr_refresh_jobs.py` must start through its documented
    `uv run --with pymupdf,pillow` form from the consumer root, which proves
    `pdfx.status` resolves from the module source (the tool install does not
    provide the import)."""
    result = subprocess.run(
        [
            *UV_RUN,
            str(consumer["module_src"] / RUNNERS["ocr_refresh_jobs.py"]),
            "--help",
        ],
        cwd=consumer["root"],
        capture_output=True,
        text=True,
        env=_isolated_env(),
    )
    assert result.returncode == 0, result.stderr


# --------------------------------------------------------------------------- relocation constraint


def test_relocated_deployment_is_not_an_execution_route(consumer):
    """The `.agents/skills/` projection deploys the runner files verbatim, but
    their repository-topology resolution cannot work from there. This pins the
    constraint that forces the documented route: if this test ever goes green,
    the documentation may be reconsidered — until then the relocated copy is a
    documentation projection, not a runner location."""
    deployed_formula = (
        consumer["root"] / ".agents" / "skills" / "formula-repair" / "formula_repair_runner.py"
    )
    assert deployed_formula.is_file(), "APM deploys skill files verbatim"
    with pytest.raises(ValueError, match="not found"):
        _load_module(deployed_formula)._audit_command(
            consumer["root"] / "audit-probe.pdf",
            consumer["root"] / "audit-probe.pdf",
            textbook=False,
        )
    deployed_ocr = subprocess.run(
        [
            *UV_RUN,
            str(consumer["root"] / ".agents" / "skills" / "llm-ocr-refresh" / "ocr_refresh_jobs.py"),
            "--help",
        ],
        cwd=consumer["root"],
        capture_output=True,
        text=True,
        env=_isolated_env(),
    )
    assert deployed_ocr.returncode != 0, (
        "the relocated ocr runner started; the no-launcher route may now "
        "point at the deployed projection instead"
    )
    assert "No module named 'pdfx'" in deployed_ocr.stderr
