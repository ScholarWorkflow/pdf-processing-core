# Codex compatibility

This repository declares both `opencode` and `codex` targets in `apm.yml`.
This record describes the producer-local contract verified for this change.
It is documentation only; runtime behavior remains in the released package and
the two deployed skill-local scripts.

## APM deployment contract

Verified on 2026-09-07 with APM 0.29.0 (5ac6733), against a clean consumer
installed from the base commit and again from the final PR head:

- three agents are deployed as `.codex/agents/<name>.toml`;
- two skills are deployed under `.agents/skills/<skill>/` with their resource
  trees intact;
- the generated agent TOML contains exactly `name`, `description`, and
  `developer_instructions`;
- the source body becomes `developer_instructions`, with only the conversion's
  leading-newline normalization.

The source OpenCode frontmatter remains authoritative for OpenCode. Codex
conversion does not carry `mode`, `hidden`, `permission`, or task restrictions
into the generated TOML.

## Metadata loss and orchestration conventions

The source agents therefore carry the intent that must survive conversion in
their bodies:

- `formula-repair` states that it is internal-only and may invoke its
  documented worker;
- `llm-ocr-refresh-unit-worker` is internal-only and must not spawn or delegate
  the unit job;
- `pdf-to-text` must not spawn sub-agents and performs its consumer-owned
  conversion directly.

These body-level statements are orchestration conventions, not ACLs or
security boundaries. Codex may not enforce the source OpenCode permission map
per agent, so no permission is widened to imitate equivalence.

## Python runtime authority

The repo-owned Python files are PEP 723 scripts. Their inline dependency
metadata is bounded to the released distribution:

```text
scholar-workflow-pdfx>=0.1.0,<0.2
```

Resolve each script from the loaded/deployed skill directory and run it with:

```bash
uv run --script "<resolved skill dir>/<script>.py" ...
```

Direct kernel examples use the same released package through:

```bash
uvx --from 'scholar-workflow-pdfx>=0.1.0,<0.2' pdfx ...
```

No command depends on the producer checkout, a shared producer environment,
the caller's current directory, or a launcher-specific runtime.

## Verified limitations

The clean APM conversion reproduced one relevant limitation: generated Codex
agent TOML does not contain the OpenCode `mode`, `hidden`, `permission`, or
task-restriction fields. The body conventions above are consequently required
for orchestration intent, but they cannot provide per-agent security
enforcement.

The acceptance record for this PR also records the exact Codex CLI version,
fresh-session probes, and any model-fidelity result separately from conversion
correctness. A weak child response must not cause business rules or permissions
to be changed.

## Producer independence

The formal runtime path has no dependency on consumer-side migration helpers,
central catalogs, bootstrap files, normalizers, overrides, or acceptance
runtimes. The producer exports only its APM files, released package contract,
and skill-local PEP 723 resources.

## Recorded tool versions

```text
APM:     0.29.0 (5ac6733)
Codex:   codex-cli 0.153.4
OpenCode: 1.18.21
uv:      0.12.5
Python:  3.14.7
```
