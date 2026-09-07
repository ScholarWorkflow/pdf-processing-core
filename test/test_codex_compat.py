"""Regression tests for the producer-local Codex/APM compatibility contract."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / ".apm" / "agents"
SKILLS = ROOT / ".apm" / "skills"
EXPECTED_AGENTS = {
    "formula-repair",
    "llm-ocr-refresh-unit-worker",
    "pdf-to-text",
}
EXPECTED_PERMISSIONS = {
    "formula-repair": {
        "read": "allow", "glob": "allow", "grep": "allow", "write": "allow",
        "edit": "allow", "bash": "allow", "task": "allow", "todowrite": "allow",
        "question": "allow", "external_directory": "allow",
    },
    "llm-ocr-refresh-unit-worker": {
        "read": "allow", "glob": "allow", "grep": "allow", "write": "allow",
        "edit": "deny", "bash": "allow", "task": "deny", "todowrite": "deny",
        "question": "deny", "external_directory": "allow",
    },
    "pdf-to-text": {
        "read": "allow", "glob": "allow", "grep": "allow", "bash": "allow",
        "todowrite": "allow",
    },
}
CENTRAL_RUNTIME_MARKERS = (
    "scholarflow-codex",
    "bootstrap-codex",
    "codex_agent_normalize",
    "codex-agents-override",
    "codex-runtime-acceptance",
    "scholarflow-skillrepo",
)


def _scalar(value: str):
    value = value.strip()
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def parse_agent(path: Path) -> tuple[dict, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines and lines[0] == "---", f"{path}: missing frontmatter opener"
    closing = next((i for i in range(1, len(lines)) if lines[i] == "---"), None)
    assert closing is not None, f"{path}: missing frontmatter closer"
    frontmatter: dict = {}
    nested: dict | None = None
    for line in lines[1:closing]:
        if not line.strip():
            continue
        if line[0].isspace():
            assert nested is not None, f"{path}: unexpected nested field"
            key, separator, value = line.strip().partition(":")
            assert separator, f"{path}: malformed nested field"
            nested[key] = _scalar(value)
            continue
        key, separator, value = line.partition(":")
        assert separator, f"{path}: malformed frontmatter field"
        if value.strip():
            nested = None
            frontmatter[key] = _scalar(value)
        else:
            nested = {}
            frontmatter[key] = nested
    return frontmatter, "\n".join(lines[closing + 1 :])


def agent_sources() -> dict[str, tuple[dict, str]]:
    paths = sorted(AGENTS.glob("*.agent.md"))
    assert {path.name.removesuffix(".agent.md") for path in paths} == EXPECTED_AGENTS
    return {
        path.name.removesuffix(".agent.md"): parse_agent(path)
        for path in paths
    }


def convention_violations(frontmatter: dict, body: str) -> list[str]:
    violations: list[str] = []
    if frontmatter.get("hidden") is True:
        if "Internal-only agent" not in body:
            violations.append("missing internal-only convention")
        if (
            "not a security boundary" not in body
            and "not ACLs or security boundaries" not in body
        ):
            violations.append("internal-only convention is presented as an ACL")
    if frontmatter.get("permission", {}).get("task") != "allow":
        if "Do not spawn sub-agents" not in body:
            violations.append("missing no-spawn convention")
    return violations


def test_agent_source_and_opencode_permission_contract_is_frozen():
    sources = agent_sources()
    for name, (frontmatter, body) in sources.items():
        assert frontmatter["name"] == name
        assert frontmatter["mode"] == "subagent"
        assert frontmatter["description"].strip()
        assert body.strip()
        assert frontmatter.get("permission") == EXPECTED_PERMISSIONS[name]
    assert sources["formula-repair"][0]["permission"]["task"] == "allow"
    assert sources["llm-ocr-refresh-unit-worker"][0]["permission"]["task"] == "deny"
    assert "Do not spawn sub-agents" in sources["pdf-to-text"][1]


def test_verified_apm_conversion_keeps_only_identity_and_body():
    for name, (frontmatter, body) in agent_sources().items():
        converted = {
            "name": frontmatter["name"],
            "description": frontmatter["description"],
            "developer_instructions": body.lstrip("\n"),
        }
        assert set(converted) == {"name", "description", "developer_instructions"}
        assert converted["name"] == name
        assert converted["description"].strip()
        assert converted["developer_instructions"].strip()
        assert not convention_violations(frontmatter, body)


def test_conversion_convention_validation_is_non_vacuous():
    for name, (frontmatter, body) in agent_sources().items():
        if frontmatter.get("hidden") is True:
            mutated = body.replace("Internal-only agent", "[removed]", 1)
            assert convention_violations(frontmatter, mutated)
        if frontmatter.get("permission", {}).get("task") != "allow":
            mutated = body.replace("Do not spawn sub-agents", "[removed]", 1)
            assert any("no-spawn" in item for item in convention_violations(frontmatter, mutated)), name


def test_skill_identity_and_codex_compatibility():
    for skill in sorted(SKILLS.iterdir()):
        if not skill.is_dir():
            continue
        frontmatter, body = parse_agent(skill / "SKILL.md")
        assert frontmatter["name"] == skill.name
        assert {item.strip() for item in frontmatter["compatibility"].split(",")} >= {"opencode", "codex"}
        assert body.strip()


def test_apm_targets_and_no_producer_mcp_declaration():
    apm = (ROOT / "apm.yml").read_text(encoding="utf-8")
    assert "targets: [opencode, codex]" in apm
    assert not re.search(r"^mcp:", apm, re.MULTILINE)


def test_runner_scripts_are_pep723_and_package_bound():
    for runner in (
        SKILLS / "formula-repair" / "formula_repair_runner.py",
        SKILLS / "llm-ocr-refresh" / "ocr_refresh_jobs.py",
    ):
        text = runner.read_text(encoding="utf-8")
        assert "# /// script" in text
        assert 'scholar-workflow-pdfx>=0.1.0,<0.2' in text
        assert "sys.path.insert" not in text
        assert "lib/pdfx" not in text
        assert "--with pymupdf" not in text
    ocr_runner = (SKILLS / "llm-ocr-refresh" / "ocr_refresh_jobs.py").read_text(encoding="utf-8").lower()
    assert '"pymupdf>=1.24"' in ocr_runner
    assert '"pillow"' in ocr_runner


def test_active_runtime_surfaces_exclude_consumer_control_plane():
    surfaces = [ROOT / "apm.yml", ROOT / "CODEX-COMPATIBILITY.md"]
    surfaces.extend(AGENTS.glob("*.agent.md"))
    surfaces.extend(SKILLS.rglob("*.md"))
    surfaces.extend(SKILLS.rglob("*.py"))
    for surface in surfaces:
        text = surface.read_text(encoding="utf-8")
        for marker in CENTRAL_RUNTIME_MARKERS:
            assert marker not in text, f"{surface}: runtime marker {marker!r}"
