---
name: formula-repair
description: Formula correctness repair orchestrator. Runs audit -> repair -> verification, refuses to release non-ok regions, and supports caller-provided batch manifests. The refresh skill is the only repairer.
compatibility: opencode
license: MIT
metadata:
  author: custom
  version: 0.1.0
---

# formula-repair: Audit, Repair, Verify

This skill makes "verify before a caller consumes formulas" enforceable. Audit facts live in `.faudit.json`; repair is delegated to `llm-ocr-refresh`; this skill schedules work and will not release a partially repaired source.

## Invariant

> **Repair complete = every audited region is `ok`.**

- Trigger on every non-ok region (`suspect`, `unverified`, `pending_l3`) plus any refresh-layer quality trigger.
- Completion requires the refresh completion marker and a fresh audit showing all regions `ok`.
- Missing completion, worker interruption, or an incomplete unit is an exception. Recover up to 3 times; exhaustion is explicitly `degraded` and requires human intervention. Never silently pass or permanently preserve a suspect state.

## Target resolution

Accept any of the following, then resolve a source document and its paired PDF:

| Caller input | Resolution |
|---|---|
| Source document path | Use the supplied path and caller metadata for its paired PDF. |
| Paired PDF path | Use it directly; locate a source document only through the documented sibling mapping. |
| Paired source/PDF record | Use the exact paths in the record. |
| Caller audit manifest | Process each entry's `pdf` and `tiers`. |
| Source identifier | Resolve through caller metadata or an explicit bounded mapping; never scan an unbounded root. |

## `check` pre-consumption command

Call before a caller consumes a formula-bearing source:

```bash
pdfx formula-check "<target>" --json
```

`<target>` may be a paired PDF or source document. The command resolves the pair and returns:

| Verdict | Meaning | Caller behavior |
|---|---|---|
| `ok` | All regions are verified. | Release the source for consumption. |
| `suspect`, `unverified`, `pending_l3` | Non-ok regions exist. | Enter the repair-and-verify loop. |
| `no_sidecar` | The audit sidecar is absent. | Generate it with `formula-audit`, then classify. |
| `error` | The target cannot be resolved. | Report the error to the caller. |

`check` stops at a verdict; a caller that needs repair invokes the repair path. A caller never receives a partially repaired source.

## Audit

For a source/PDF pair:

```bash
pdfx formula-audit "<paired PDF>" --extraction-dir "<source root or omit>" --project
```

- `--project` idempotently projects the aggregate verdict into source metadata.
- `--layout` may be used for image-heavy documents so untrusted or empty pages can receive layout regions when the configured capability is available.
- A matching fingerprint reuses `.faudit.json`; `--force` re-runs the audit.
- Detailed page logs belong in the runtime log. Read aggregate counts, source-unit verdicts, and region verdicts rather than repeatedly returning full page arrays.

## Batch repair: runner plus unit worker

The runner creates `<source_root>/.formula-repair-state.json` and transient `.ocr_units/` files. It partitions work rather than placing a whole source or many pages into one worker:

- one PDF job has at most 12 page units or 20 region units;
- auxiliary-source jobs have at most 8 pages;
- workers write only their own `RESULT.json`;
- source documents remain unchanged until every job for the paired PDF succeeds;
- final merge, re-audit, and final check run once;
- a source fingerprint change causes replanning and never overwrites an external change or consumes a retry;
- successful finalization removes OCR unit prose and keeps short state records;
- interrupted `running` jobs return to `pending` while retaining attempts; the third failure becomes `degraded` and ordinary resume does not reopen it.

Invoke the repository-owned runner through the public runtime entry point:

```bash
skillrepo exec pdf-processing-core .apm/skills/formula-repair/formula_repair_runner.py
```

The refresh skill's unit-worker mode reads job JSON, uses the configured visual-OCR provider abstraction, writes unit results, and never writes source documents, audit sidecars, manifests, or downstream indexes. A worker does not choose a provider or model.

## Queue, lanes, and recovery

The runner's `queue --profile <fingerprint>` reads the matching benchmark profile and preallocates read-only lane queues. It does not read secrets or provider configuration. An unmatched profile uses 1 worker and 1 job per lane.

Drive each lane with `lane-next --lane <file>`:

- `claimed`: invoke the registered `llm-ocr-refresh-unit-worker` with the job file and reuse the lane session;
- `await_result`: run `collect`; if `RESULT.json` is absent, resume the same session up to 2 times before creating a new worker;
- `lane_stopped`: stop the lane for this round; leave remaining jobs pending;
- `wait`: pause briefly and ask `lane-next` again;
- `done`: finish the lane.

One PDF occupies one lane per round and its page segments run sequentially. A failed job stops only its lane for that round; the next queue round redistributes pending work fairly. Never schedule one worker per page or claim a running job again.

If the unit-worker registration is unavailable, run `recover` for already claimed jobs and return `worker_registration_unavailable`. Do not substitute a general worker, classify registration failure as OCR failure, or silently continue.

## Repair and verification

1. Read the audit sidecar and collect non-ok regions. Regions with bboxes become region units; untrusted, empty, or page-level unverified content becomes page units.
2. Triage pending L3 regions before OCR:
   - trusted/washable layer and `eligible: false`: leave them for the normal repair chain;
   - `low_risk[]`: apply `status: "passed"`, `method: "low-risk-rule"` with no OCR;
   - sample at most 3 high-risk fingerprints in stable order as one `l3_verify` job;
   - all consistent: apply `method: "risk-sample"` without changing the source;
   - any inconsistency: escalate the inconsistent fingerprint and every remaining high-risk fingerprint, pass stored crops to repair, and avoid reading the same bbox twice.
3. Queue small unit jobs. The worker stores OCR prose only under its unit directory and returns member-level results, monotonic timestamps, patches, and numeric counters.
4. Collect results and recover missing or failed work according to the lane rules.
5. After the refresh completion marker, finalize atomically and run a fresh audit:

```bash
pdfx formula-audit "<paired PDF>" --force --project
```

6. Release only when every region is `ok`. Otherwise retry failed units up to 3 times; then return `degraded` with the reason.

## Contracts and boundaries

- The runner is the only manifest writer and the only batch source writer.
- Workers receive exactly one job JSON at a time and write only their unit directory and result file.
- Patches are non-overlapping, byte-offset based, and valid only for the source fingerprint that produced them.
- No OCR prose, formula reasoning, provider names, secrets, or configuration values enter dispatcher context or final reports.
- Auxiliary-source checks are available when caller metadata supplies an auxiliary source; unresolved conflicts trigger targeted re-reads, not whole-source rereads.
- Existing clean pages and regions remain byte-identical. No formatting cleanup is allowed outside mandatory units.
- Missing files or invalid directories are caller data errors, not repair verdicts.

## Batch manifest semantics

For a caller-provided list, audit each paired PDF, collect non-ok units, run the bounded queue, and perform one final sweep. The acceptance condition is all source documents clean, all `.faudit.json` regions `ok`, and the source metadata projection updated.
