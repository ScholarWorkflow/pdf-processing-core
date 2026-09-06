# Codex Compatibility (producer-local)

This repo declares `targets: [opencode, codex]` in `apm.yml`. This document
records what producer-local Codex compatibility means here, what is expressed
where, and which OpenCode runtime properties have no equivalent in the current
Codex CLI. It is documentation only: nothing in this repo depends, at runtime,
on any consumer-side migration tooling.

## What a Codex consumer install carries

`apm install --target codex` deploys:

- one `.codex/agents/<name>.toml` per `.apm/agents/*.agent.md`, converting
  exactly the `name`, `description`, and body (as `developer_instructions`);
  every other frontmatter key is dropped;
- one `.agents/skills/<skill>/` directory per `.apm/skills/<skill>/`.

Verified against APM 0.29.0 with this repo: the deployed TOML contains only
`name`, `description`, `developer_instructions`; the body is carried verbatim
with one leading newline stripped.

Because the frontmatter ACL block is dropped by the conversion, every Codex
agent convention this repo needs is carried by the agent body itself:

| OpenCode frontmatter | Codex expression (in the body) |
|---|---|
| `hidden: true` | "Internal-only agent ... orchestration convention, not a security boundary." |
| `permission.task` absent or `deny` | "Do not spawn sub-agents (no task calls)." |
| `permission.task: allow` | (no restriction line; the agent may spawn its documented worker) |
| `skillrepo exec ...` call site | same call site, plus the deployed-skill-path resolution rule |

The runtime entry points referenced as `skillrepo exec pdf-processing-core
.apm/skills/...` stay canonical for OpenCode. Each call site also states the
harness-neutral resolution used when no `skillrepo` launcher exists: execute
the same repository-owned file from its APM deployment location
(`<consumer root>/.agents/skills/<skill>/...`).

## Recorded limitations (Codex CLI 0.153.x)

The following OpenCode runtime security properties cannot be expressed by this
repo for Codex today. They are recorded, not worked around: no permission is
widened, and no substitute enforcement is claimed.

1. **Per-agent tool ACL.** The OpenCode `permission` map (`read`, `write`,
   `edit`, `bash`, `task`, `todowrite`, `question`, `glob`, `grep`,
   `external_directory`) has no Codex equivalent. codex-cli 0.153.x applies
   only `developer_instructions`, `model`, `model_reasoning_effort`, and
   feature/skills toggles per agent; `sandbox_mode`, `mcp_servers`, and
   `web_search` in an agent file are parsed but stripped from the spawned
   session's config layer, so a child inherits the parent session's sandbox,
   MCP, and web surface. The behavioral prohibitions (for example the unit
   worker writing only under its `unit_dir`) remain body-level conventions,
   not enforceable ACLs.
2. **`hidden` as ACL.** Codex has no hidden-agent mechanism; `hidden: true`
   survives only as the internal-only orchestration convention.
3. **`mode: subagent`.** Codex custom agents are always addressed by exact
   name through the spawn mechanism; the mode flag is dropped with no semantic
   loss.
4. **Per-agent network/web grants.** OpenCode `websearch`/`webfetch`
   permissions cannot be scoped per agent; all three agents declare no web
   grants in OpenCode frontmatter, and the bodies require none.

The reviewed consumer-side mapping for these three agents (workspace-write
sandbox envelope, empty MCP whitelist, web disabled) was produced during the
ScholarWorkflow consumer migration and is treated as validated reference
evidence only; this repo embeds none of it and does not depend on it.

## Compatibility contract tests

`test/test_codex_compat.py` pins the install contract: agent frontmatter and
identity, body-carried conventions that must survive the lossy conversion,
runtime entry points resolving to existing producer skill files, the
harness-neutral skill metadata, the both-targets `apm.yml` with no producer
MCP declarations, and the absence of any consumer-runtime dependency in the
runtime surfaces.
