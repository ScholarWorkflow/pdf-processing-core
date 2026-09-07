from __future__ import annotations

import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / ".apm/skills"
RELEASE_DEPENDENCY = "scholar-workflow-pdfx>=0.1.0,<0.2"


def _pep723_block(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.search(r"^# /// script\n(?P<body>.*?)^# ///$", text, re.MULTILINE | re.DOTALL)
    return match.group("body") if match else ""


def _imports_pdfx(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == "pdfx" or alias.name.startswith("pdfx.") for alias in node.names):
                return True
        if isinstance(node, ast.ImportFrom):
            if node.module == "pdfx" or (node.module and node.module.startswith("pdfx.")):
                return True
    return False


def test_pdfx_importers_have_bounded_pep723_dependency():
    importers = [path for path in SKILLS.rglob("*.py") if _imports_pdfx(path)]
    assert importers
    for path in importers:
        assert RELEASE_DEPENDENCY in _pep723_block(path), path


def test_subprocess_pdfx_consumer_has_bounded_pep723_dependency():
    runner = SKILLS / "formula-repair/formula_repair_runner.py"
    text = runner.read_text(encoding="utf-8")
    assert "formula-audit" in text
    assert RELEASE_DEPENDENCY in _pep723_block(runner)


def test_skill_code_does_not_load_pdfx_from_repository_layout():
    forbidden = ("lib/pdfx", "_pdfx_lib_dir", "sys.path.insert")
    for path in SKILLS.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not any(marker in text for marker in forbidden), path


def test_skill_python_entry_points_use_uv_script_runtime():
    contracts = list(SKILLS.glob("*/SKILL.md"))
    assert contracts
    for path in contracts:
        text = path.read_text(encoding="utf-8")
        assert "skillrepo exec" not in text
        assert not re.search(r"(?m)^\s*(?:python|python3|uv run --with)\b.*\.py\b", text)
        assert re.search(r"uv run --script \.apm/skills/[^ ]+\.py", text), path


def test_direct_pdfx_cli_examples_resolve_released_distribution():
    for path in SKILLS.glob("*/SKILL.md"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"(?m)^\s*pdfx\s+(?:quality|scan-math|formula-check|formula-audit)\b", text)
        direct_examples = re.findall(r"(?m)^\s*(uvx .*? pdfx\s+[^\n]+)", text)
        assert direct_examples, path
        assert all("--from 'scholar-workflow-pdfx>=0.1.0,<0.2'" in line for line in direct_examples)
