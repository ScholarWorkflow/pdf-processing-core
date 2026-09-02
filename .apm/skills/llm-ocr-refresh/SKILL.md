---
name: llm-ocr-refresh
description: Refreshes unreliable PDF-derived source documents. Quality audits and math scanning identify mandatory page or region units; a configured visual-OCR provider chain repairs only those units and atomically writes back clean text and LaTeX. Supports caller metadata and optional auxiliary sources.
compatibility: opencode
license: MIT
metadata:
  author: custom
  version: 1.4.0
---

# llm-ocr-refresh: Targeted Source Repair

Restore damaged PDF-derived source documents to clean text, correct LaTeX mathematics, and existing visual annotations. The skill repairs only evidence-backed bad units, preserves good bytes, and writes back only after explicit validation.

## Target resolution

Accept any of the following and resolve a source document plus its paired PDF:

| Caller input | Resolution |
|---|---|
| Source document path | Use it directly and resolve its paired PDF from caller metadata or the documented sibling mapping. |
| Paired PDF path | Use it directly and locate the source document only through the documented mapping. |
| Paired source/PDF record | Use the exact paths and metadata in the record. |
| Source identifier | Resolve through caller metadata or a bounded explicit mapping; never scan an unbounded data root. |
| Optional auxiliary source | Use only when supplied by the caller and only for targeted cross-checks. |

If the source already has `llm_ocr: true`, skip it as a cache hit unless the caller explicitly supplies `force=true`.

## Repair decision

The goal is for every repaired page to match the paired PDF, with formulas represented as correct LaTeX. Three evidence layers determine mandatory units.

### Layer 1: quality tiers

Run the shared `pdfx` kernel:

```bash
pdfx quality "<paired PDF absolute path>" --json
```

Use the per-page tier and image count to create page units:

| Page condition | Action |
|---|---|
| `untrusted` text layer | Mandatory repair. |
| `empty` with images | Mandatory repair; it may be a scanned or visual-only page. |
| `empty` without images | Keep unchanged; it is likely blank or non-content. |
| `trusted` or `washable` | No repair solely for formatting noise. |

Thresholds and tier definitions belong to the installable `pdfx` quality package, not to this skill.

### Layer 2: content-level math triggers

For ambiguous plain-text mathematics, run:

```bash
pdfx scan-math "<source document absolute path>" --pdf "<paired PDF absolute path>" --json
```

The kernel performs ambiguity scanning, maps hits to physical pages, and emits region units or page upgrades. Existing LaTeX-delimited spans are protected. If no paired PDF exists, process all hits as page units.

When `.faudit.json` exists, it is authoritative: every non-ok region (`suspect`, `unverified`, `pending_l3`) becomes a mandatory region or page unit. `scan-math` remains the lightweight path when no audit sidecar exists.

Images or visual annotations do not by themselves trigger OCR. Existing annotations are preserved; the refresh skill does not invent, rewrite, or validate them.

### Layer 3: semantic audit suspects

Quality tiers cannot reliably detect semantically corrupted but legal characters. Read the source metadata audit result or the caller-provided suspect-page list before accepting a page:

1. Pages marked `suspect` by the semantic audit are mandatory page units regardless of quality tier.
2. If the caller already has the audit report, pass its page list directly and avoid a second parse.
3. Repair and verification use the same page path as other bad units; a failed or unchanged audit is reported as unresolved rather than silently accepted.

### Decision

- No hits in any layer -> do nothing.
- Mandatory units are the union of semantic-audit suspect pages, quality bad pages, and formula-audit non-ok regions or pages. If a page has both page and region reasons, merge them into one page unit.
- Repair only mandatory units. Preserve every other source byte.
- A matching `llm_ocr: true` marker skips work unless forced; force still limits work to current mandatory units.

## Integrated batch execution

The repository runner owns rendering, provider calls, unit persistence, state, review planning, and final atomic merge. The caller supplies the mandatory-unit file; the dispatcher does not manually operate on individual images.

State the separation clearly: the integrated refresh route and the formula-repair unit-worker route are both supported. The former owns its own batch state and finalize operation; the latter is invoked by the formula-repair runner and writes only unit results.

1. The caller or an upstream audit writes `repair_units.json` using schema `ocr-refresh-repair-units/1`:

   `unit_id`, `kind` (`page|region`), `page`, `bbox_pt`, `read_kind` (`first|second|third`), `reason`, and optionally `anchor_line`, `anchor_text`, `parent_unit_id`, or `zoom_bbox_pt`.

   The refresh runner consumes this file. It does not recalculate or overwrite quality, semantic-audit, formula-audit, or pending-L3 decisions.

2. Run the batch:

   ```bash
   skillrepo exec pdf-processing-core .apm/skills/llm-ocr-refresh/ocr_refresh_jobs.py run \
     --target "<source document or paired source>" --pdf "<paired PDF>" \
     --units "<repair_units.json>" --state "<source>.ocr_repair_state.json"
   ```

   Page units render the full page with the standard PDF render transform. Region units expand the supplied `bbox_pt` by the documented margin and render only that clip. Matching unit keys, image hashes, and render parameters reuse existing images. The runner writes images, OCR results, and result JSON below `<source_root>/.ocr_units/`; the parent process alone writes state atomically.

3. `run` never writes the source. The caller reads unit results and state, then submits a batch review plan with `read_kind=second|third`. A second read reuses a matching crop; a page review uses an explicitly supplied local bbox rather than rereading the whole page. Three reads do not auto-authorize a result; unresolved units remain pending until the caller decides.

4. After all mandatory units have acceptable results, submit an acceptance plan:

   ```bash
   skillrepo exec pdf-processing-core .apm/skills/llm-ocr-refresh/ocr_refresh_jobs.py finalize \
     --state "<state>" --accept "<accept_plan.json>"
   ```

   The runner verifies complete unit coverage, completed review plans, non-overlapping patches, page boundaries, structural validity, and zero unresolved units. It then uses a temporary file plus `os.replace` to merge atomically and writes the `llm_ocr` fields. Any validation failure leaves the source bytes and marker unchanged.

## Rendering and provider abstraction

1. Use the configured visual-OCR capability and its provider chain. This skill does not select providers, models, endpoints, quotas, or deployment settings.
2. Render page units as full pages and region units as margin-expanded crops. A region crop must not depend on a separate ad hoc region command.
3. Provider/runtime fallback, rate limiting, timeout handling, and counters stay inside the runner and provider abstraction. A failed unit is recorded; outer logic does not spin indefinitely.
4. Existing visual-annotation lines are moved unchanged to the end of a replaced page when the source contract requires it. Missing annotations do not trigger OCR, and OCR does not generate annotations.
5. Writeback rules:
   - good pages and regions stay byte-identical;
   - page units replace the complete page block while preserving the source's page order and existing page markers;
   - region units replace only a uniquely located `anchor_line` or `anchor_text` line;
   - missing or multiple anchors promote the page to an explicit page-level upgrade plan;
   - never reformat or polish content outside mandatory units.

## Targeted second reads

Perform an independent second read only when a trigger exists:

1. first OCR contains `[?]`;
2. source text, OCR, or auxiliary source disagree on a key expression;
3. formula audit marks a high-risk signal;
4. the symbol sequence is visibly incomplete or confidence is low.

The caller submits a batch plan. Region reads reuse matching crops; page reads use a local enlarged bbox. A third read is used only for a second-read conflict. If the third read cannot resolve the unit, retain `[?]` or pending status and do not write `llm_ocr`.

Differences may be normalized for reporting, but comparison does not decide which formula is correct; that decision requires caller review of the PDF evidence.

When an auxiliary source is supplied, use the matching item only to cross-check the affected expression. Never let an auxiliary source replace the PDF evidence or expand a targeted read into a full-document reread.

## Source writeback

The source's existing YAML frontmatter fields are all preserved. Finalization appends the refresh fields:

```yaml
llm_ocr: true
llm_ocr_model: "<runtime-selected model identifier>"
llm_ocr_date: "YYYY-MM-DD"
llm_ocr_source: "<paired PDF relative path> all N pages"
llm_ocr_note: "Review record; unresolved content remains marked [?]."
```

The model field is a runtime output field only; no model, provider, cost, or endpoint is prescribed here. Source pagination and existing page markers remain unchanged. Only mandatory page blocks or uniquely anchored region lines are replaced.

If any mandatory unit fails, do not write `llm_ocr: true`. The runner may retain successful unit results or patches in transient state for recovery, but the source is changed only by a validated atomic finalize.

## Caller handoff

This skill owns source repair, not downstream indexing or application-specific synchronization. On success, return the source path, paired PDF, state path, changed units, audit status, and any caller metadata needed for the caller's own indexing or cache refresh. Do not read or write a caller-owned index unless its explicit contract is supplied as input.

## Verification and report

Report:

- quality-tier counts;
- mandatory units and their generic trigger (`semantic_audit`, `quality`, `empty_with_images`, `formula_audit`, or `scan_math`);
- failed or unresolved units;
- targeted second or third reads;
- source fingerprint before and after;
- final state path and audit verdict;
- caller handoff fields, without OCR prose or provider secrets.

## Boundaries

- Repairs are limited to paired PDF pages; an unsplit combined PDF must be mapped to the target page range by caller metadata before use.
- The skill does not migrate, index, or bulk-transform caller data.
- Missing paths and invalid directory mappings are caller data errors.
- Formula correctness is accepted only after the final audit reports every relevant region as `ok`.
