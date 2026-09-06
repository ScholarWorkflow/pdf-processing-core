"""Codex/producer compatibility contract tests.

`apm install --target codex` converts each `.apm/agents/*.agent.md` into a
`.codex/agents/<name>.toml` skeleton carrying EXACTLY `name`, `description`,
and the body as `developer_instructions` (one leading newline stripped); every
other frontmatter key — including `mode`, `hidden`, and the whole `permission`
ACL map — is dropped. Skills deploy verbatim to `.agents/skills/<skill>/`.

These tests pin the contracts that must hold for a clean Codex consumer to
discover and run this repo's agents after that lossy conversion:

- agent frontmatter/identity stays well-formed for both harnesses;
- the conventions Codex needs (internal-only, no-spawn) live in the BODY,
  because the frontmatter that used to carry them is dropped;
- every doc surface that consumes this repo's package expresses the runtime as
  the producer uv project contract (`uv run --project "<pdf-processing-core
  project root>" --locked ...`) with the APM producer-root resolution, and no
  surface injects dependencies ad hoc (`--with pymupdf`) or demotes the
  deployed `.agents/skills/` copy to a non-executable projection;
- the Python project contract that makes the installed-package route real:
  `pyproject.toml` (project name, `lib/pdfx` wheel package, `pdfx.cli:main`
  entry point, `PyMuPDF` dependency) plus a committed `uv.lock`;
- skill metadata claims both harnesses;
- `apm.yml` keeps both targets and declares no MCP (producers never do);
- the OpenCode permission semantics are preserved unchanged (no widening);
- no runtime surface references consumer-side migration tooling.

The conversion is simulated here exactly as APM performs it (verified against
APM 0.29.0 with this repo), so a regression that would break a deployed Codex
consumer fails in CI without needing an install.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AGENTS_DIR = ROOT / ".apm" / "agents"
SKILLS_DIR = ROOT / ".apm" / "skills"

# Document text surfaces that are deployed into consumers and must never
# reference the consumer-side migration tooling.
RUNTIME_SURFACES = (
    ROOT / "apm.yml",
    *sorted(AGENTS_DIR.glob("*.agent.md")),
    *sorted(SKILLS_DIR.glob("*/SKILL.md")),
    *sorted(SKILLS_DIR.glob("*/*.py")),
)

CONSUMER_TOOLING_MARKERS = (
    "scholarflow-codex",
    "bootstrap-codex",
    "codex_agent_normalize",
    "codex-agents-override",
    "scholarflow-skillrepo",
    "codex-runtime-acceptance",
)

# Where APM clones this repo's producer project inside a consumer; this is the
# producer project root resolution every runtime doc surface must carry.
MODULE_SOURCE_ROUTE = "apm_modules/ScholarWorkflow/pdf-processing-core/"

# The runtime boundary for repo-owned Python work, identical in both harnesses:
# the producer project environment (APM deploys files; Python dependencies come
# from pyproject.toml/uv.lock, not from script location or ad hoc injection).
UV_PROJECT_CONTRACT = 'uv run --project "<pdf-processing-core project root>" --locked'

# Doc surfaces that consume this repo's own package at runtime. pdf-to-text is
# deliberately excluded: its conversion command is a consumer-owned contract.
PRODUCER_RUNTIME_DOC_SURFACES = (
    AGENTS_DIR / "formula-repair.agent.md",
    SKILLS_DIR / "formula-repair" / "SKILL.md",
    SKILLS_DIR / "llm-ocr-refresh" / "SKILL.md",
    ROOT / "CODEX-COMPATIBILITY.md",
)

# The repo-owned runner scripts, which must be plain consumers of the
# installed package (see test_codex_runner_relocation.py for the execution
# proof against a simulated clean consumer).
RUNNER_FILES = (
    SKILLS_DIR / "formula-repair" / "formula_repair_runner.py",
    SKILLS_DIR / "llm-ocr-refresh" / "ocr_refresh_jobs.py",
)


def _parse_scalar(value: str) -> str | bool:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    if value == "true":
        return True
    if value == "false":
        return False
    return value


def parse_frontmatter(path: Path) -> tuple[dict, str]:
    """Parse `---` frontmatter into a dict, returning (frontmatter, body).

    Supports exactly the shapes this repo uses: top-level `key: scalar`
    entries and one nesting level of `key:` followed by indented scalars.
    Anything else is a contract failure — agent frontmatter must stay simple
    enough for both harness frontmatter parsers.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines and lines[0].strip() == "---", f"{path}: missing frontmatter opener"
    closing = next(
        (i for i in range(1, len(lines)) if lines[i].strip() in ("---", "...")),
        None,
    )
    assert closing is not None, f"{path}: missing frontmatter closer"

    fm: dict = {}
    current_sub: dict | None = None
    for line in lines[1:closing]:
        if not line.strip():
            continue
        if line[0] in " \t":
            assert current_sub is not None, f"{path}: unexpected indented line: {line!r}"
            key, _, value = line.strip().partition(":")
            assert value.strip(), f"{path}: nested key {key!r} has no scalar value"
            current_sub[_parse_scalar(key)] = _parse_scalar(value)
        else:
            key, sep, value = line.partition(":")
            assert sep, f"{path}: malformed frontmatter line: {line!r}"
            if value.strip() == "":
                current_sub = {}
                fm[_parse_scalar(key)] = current_sub
            else:
                current_sub = None
                fm[_parse_scalar(key)] = _parse_scalar(value)
    body = "\n".join(lines[closing + 1 :])
    return fm, body


def agent_files() -> dict[str, Path]:
    return {p.name.removesuffix(".agent.md"): p for p in sorted(AGENTS_DIR.glob("*.agent.md"))}


def agent_sources() -> dict[str, tuple[dict, str]]:
    return {name: parse_frontmatter(path) for name, path in agent_files().items()}


# --------------------------------------------------------------------------- apm.yml


def test_apm_yml_declares_both_opencode_and_codex_targets():
    text = (ROOT / "apm.yml").read_text(encoding="utf-8")
    targets_line = next(
        line for line in text.splitlines() if line.strip().startswith("targets:")
    )
    declared = {
        item.strip().strip("'\"")
        for item in targets_line.partition(":")[2].strip(" []").split(",")
    }
    assert {"opencode", "codex"} <= declared


def test_apm_yml_declares_no_mcp_dependencies():
    text = (ROOT / "apm.yml").read_text(encoding="utf-8")
    assert not re.search(r"^mcp:", text, re.MULTILINE), (
        "producers must never declare MCP servers (they would silently "
        "materialize in 1-hop consumers)"
    )


# --------------------------------------------------------------------------- agents


def test_agent_frontmatter_identity_contract():
    sources = agent_sources()
    assert set(sources) == set(agent_files()), "frontmatter name must match filename"
    for name, (fm, body) in sources.items():
        assert fm.get("name") == name, f"{name}: frontmatter name mismatch"
        assert str(fm.get("description", "")).strip(), f"{name}: empty description"
        assert body.strip(), f"{name}: empty body"
        assert fm.get("mode") == "subagent", f"{name}: OpenCode mode semantics dropped"


def test_opencode_permission_semantics_unchanged_and_not_widened():
    """Scope-freeze guard: the OpenCode ACL maps stay as reviewed, and no agent
    gains web grants (the Codex envelope for all three agents is web-disabled;
    adding one would widen permissions to fake an equivalence that Codex
    cannot enforce)."""
    expected_no_spawn = {"llm-ocr-refresh-unit-worker", "pdf-to-text"}
    for name, (fm, _body) in agent_sources().items():
        permission = fm.get("permission")
        assert isinstance(permission, dict) and permission, f"{name}: permission map missing"
        assert all(v in ("allow", "deny") for v in permission.values()), (
            f"{name}: permission values must be allow/deny"
        )
        web_grants = {"websearch", "webfetch"} & set(permission)
        assert not web_grants, f"{name}: unexpected web grants {sorted(web_grants)}"
        if name in expected_no_spawn:
            assert permission.get("task") != "allow", f"{name}: task grant reappeared"


def codex_body_convention_violations(fm: dict, body: str) -> list[str]:
    """Body conventions Codex needs after the frontmatter drop, as explicit
    violations so tests can also assert the checks fire (not just pass)."""
    violations: list[str] = []
    if fm.get("hidden") is True:
        if "Internal-only agent" not in body:
            violations.append("hidden agent lacks the internal-only convention")
        if "orchestration convention, not a security boundary" not in body:
            violations.append("internal-only convention must stay explicitly non-ACL")
    if fm.get("permission", {}).get("task") != "allow":
        if "Do not spawn sub-agents" not in body:
            violations.append("task-denied agent lacks the no-spawn convention")
    return violations


def test_codex_conversion_survival_contract():
    """After APM drops the frontmatter, the deployed developer_instructions is
    the ONLY carrier of agent semantics. Every convention Codex needs must
    therefore be present in the body itself."""
    for name, (fm, body) in agent_sources().items():
        developer_instructions = body[1:] if body.startswith("\n") else body
        assert developer_instructions.strip(), f"{name}: body empty after conversion strip"
        violations = codex_body_convention_violations(fm, body)
        assert not violations, f"{name}: {violations}"


def test_hidden_internal_only_contract_fails_when_convention_removed():
    """The hidden branch in codex_body_convention_violations must be live
    code: a hidden agent whose body drops the internal-only convention has to
    be reported, proving the assertion is actually exercised."""
    hidden = {
        name: (fm, body)
        for name, (fm, body) in agent_sources().items()
        if fm.get("hidden") is True
    }
    assert hidden, "no hidden: true agent found; the internal-only contract is untested"
    for name, (fm, body) in hidden.items():
        stripped = body.replace("Internal-only agent", "[convention removed]", 1)
        assert stripped != body, f"{name}: expected to strip the convention sentence"
        violations = codex_body_convention_violations(fm, stripped)
        assert any("internal-only" in v for v in violations), (
            f"{name}: dropping the internal-only convention was not reported"
        )


# --------------------------------------------------------------------------- runtime entry points


def test_python_project_contract_pins_installed_package_route():
    """The installed-package route is only real if the repo stays a normal
    Python project: name, wheel package, console entry, runtime dependency,
    and the committed lockfile that `uv run --project ... --locked` relies on."""
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["name"] == "pdf-processing-core"
    assert pyproject["project"]["scripts"]["pdfx"] == "pdfx.cli:main"
    assert any(dep.startswith("PyMuPDF") for dep in pyproject["project"]["dependencies"]), (
        "PyMuPDF must stay a declared runtime dependency of the package"
    )
    assert "lib/pdfx" in pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    assert (ROOT / "uv.lock").is_file(), "uv.lock is the committed producer environment contract"


def test_producer_runtime_docs_use_uv_project_contract():
    """Every surface that consumes this repo's package must express the
    runtime as the producer project environment — never a launcher route, ad
    hoc dependency injection, or a 'deployment is not executable' claim."""
    for surface in PRODUCER_RUNTIME_DOC_SURFACES:
        text = surface.read_text(encoding="utf-8")
        assert UV_PROJECT_CONTRACT in text, (
            f"{surface.name}: missing the producer uv project contract"
        )
        assert MODULE_SOURCE_ROUTE in text, (
            f"{surface.name}: missing the APM producer project root resolution"
        )
        assert "skillrepo exec" not in text, (
            f"{surface.name}: the launcher route must not come back as a "
            "second dependency model"
        )
        assert "--with pymupdf" not in text, (
            f"{surface.name}: dependencies must not be injected ad hoc"
        )
        assert "must not be executed" not in text, (
            f"{surface.name}: the deployed skill copy is an execution route "
            "under the producer project environment"
        )


def test_runners_are_plain_consumers_of_the_installed_package():
    for runner in RUNNER_FILES:
        text = runner.read_text(encoding="utf-8")
        assert "sys.path.insert" not in text, f"{runner.name}: source-tree sys.path shim"
        assert "--with pymupdf" not in text, f"{runner.name}: ad hoc dependency injection"
        assert "cli.py" not in text, f"{runner.name}: source-tree cli.py resolution"


def test_skill_frontmatter_claims_both_harnesses():
    for skill_md in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        fm, body = parse_frontmatter(skill_md)
        assert fm.get("name") == skill_md.parent.name, f"{skill_md}: name/dir mismatch"
        assert str(fm.get("description", "")).strip(), f"{skill_md}: empty description"
        compatibility = {
            item.strip()
            for item in str(fm.get("compatibility", "")).split(",")
        }
        assert {"opencode", "codex"} <= compatibility, (
            f"{skill_md}: compatibility must claim both harnesses, got {compatibility}"
        )
        assert body.strip(), f"{skill_md}: empty body"


# --------------------------------------------------------------------------- consumer-runtime independence


@pytest.mark.parametrize("surface", RUNTIME_SURFACES, ids=lambda p: str(p.relative_to(ROOT)))
def test_runtime_surfaces_do_not_reference_consumer_migration_tooling(surface):
    text = surface.read_text(encoding="utf-8")
    for marker in CONSUMER_TOOLING_MARKERS:
        assert marker not in text, f"{surface.name}: runtime surface references {marker!r}"
