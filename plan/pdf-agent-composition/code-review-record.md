# PDF composition code candidate review R2

2026-10-22. Independent read-only source review; no Nix evaluation, build, installation, harness execution or formal test performed. This is not Merge READY or test PASS.

## Scope

PDF producer candidate: flake.nix, nix/pdfx.nix, docs/NIX.md against 3444dca0e0b84f46d1839ab5c707afa78e23d3a7. Exam candidate: flake.nix, nix/packages/pdf-adapted.nix, nix/packages/kakomon-folder-pipeline.nix, README.md against d57c2c47ac5d3a4dc6579bbcfb2508352e696acd.

Read the complete changed files and relevant public role/skill/wrapper sources. Read original flake/docs directly at the fixed revisions. Followed the producer factory → public package → Exam thin field adapter → folder dependency and refresh caller paths.

## Findings

No source-level blocker found in the reviewed PDF producer changes. They match approved PDF-COMPOSE-P1 and may be committed to its Draft PR as an unverified implementation.

- mkPdfPackages is the shared public construction path; normal package/devShell outputs reuse it. Existing package names, runtime return and default harness remain.
- pdf-to-text invokes caller-owned absolute wrapper directly, with check/conversion wording, allowed command list and dependency failure wording aligned. It does not acquire ownership of the consumer's batch.
- Response protocol is conditional on the role's existing JSON requirement. Normal conversion summary remains permitted. Formula and unit-worker existing schemas remain unchanged.
- Two store wrappers and resource trees remain intact. Formula runner's adjacent concurrency-benchmark.json is still included by recursive resource copying. Runtime closure is not represented as PATH injection.
- At the reviewed upstream revision, ocr_refresh_jobs.py contains the old uv invocation only in its module usage docstring, not a generated actionable command. No extra generated-command patch is needed for this fact. Original APM sources remain untouched.

Exam source changes are consistent with EXAM-PDF-COMPOSE-P1: public pdfxAgent is directly aliased, duplicated skills/roles are removed, producer runtime fields are retained for Exam wrappers, and refresh substitution keeps the old literal source while directing the output to the public store wrapper. Existing dependency/factory fields and algorithms are preserved.

Known unfinished integration prerequisite, not a newly discovered design defect: Exam still pins pdfcore 3444dca, which has no lib.mkPdfPackages. Its current code candidate therefore must not be represented as runnable, complete or mergeable. Before publishing it as completed integration, update the actual producer pin and corresponding lock entry after the producer output is genuinely available/verified, and align README. Review those actual changes before claiming readiness; no fictional SHA or fabricated lock hash.

## Status

PDF candidate: source review complete; suitable for unverified Draft implementation publication.
Exam candidate: logic review complete; final integration awaits real producer revision and lock update.
Gate2 recipe: pending actual revisions/environment/commands.
Gate3 results: not executed by this reviewer; no PASS inferred.
Merge conclusion: not assessed as ready.

No additional regression, provenance assertion, framework, JSON schema or full business test has been requested. Preserve the existing Gate1 scope. Do not use the user's Mac or claim Darwin execution from Linux evidence.

## Reviewed fingerprints

- PDF flake.nix: 2f747cce3e7c0167379e7c63ee37a88fe36d06b22112d1b4e3c80abae2da7c1e
- PDF nix/pdfx.nix: 4c838dad1ff355c7fa636030281b7461ed0d0f76839152e2c1ddcf0ab5467e61
- PDF docs/NIX.md: 3aa874fdc0ac3ac051bf8217b939b5eec2c3eb1200478f0fea5d1ca6ff6b52d3
- Exam flake.nix: d4fc151ab9c467f92a4229e9d99e443d2a861500e288facf7625b5da85e8521d
- Exam pdf-adapted.nix: 9e57a8e1eeb1865085c523706d5700846013a266fe169b677cebf0574b346678
- Exam kakomon-folder-pipeline.nix: 14ad87e7a58f2fbe1f3f4d3e2fd919dcd730d7a992e1215adbe2a909d01db9cf
- Exam README.md: 2141263d9d6a01c4d8c14dd2a81750718056fdc9d0934f0753396c527e6d7a08

These fingerprints identify reviewed candidates only; they are not additional product provenance requirements.
