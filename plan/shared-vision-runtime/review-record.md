# Candidate review and validation record

Base: `0d2348a1438edba24f966b86ab41c16bc4015edd` (merged #14).

Independent read-only review examined the complete candidate diff and extraction, TOC, audit, refresh and shared-client call chains. Two concrete issues were corrected and rechecked statically:

- Role-model overrides could restore a structured:false deployment when several deployments share a model name. Resolve the override then preserve the selected deployment name; a parameterized targeted test covers the combination.
- A shared generic model could accept the dedicated layout adapter's empty prompt and return a description instead of OCR. Without an explicit legacy endpoint or PDFX_LAYOUT_MODEL, that adapter is unavailable and the existing extraction chain falls through to the transcription prompt. The targeted adapter check preserves the exact prompt; extraction fallback was also inspected.

Status: NOT_READY pending upstream integration and final merge-constraint verification. The user explicitly authorized local dependency installation, tests and simulated HTTP services on 2026-11-04. Python observations are recorded in test-results.md; they are not a final independent approval. No external service/model was called.

Base runtime candidate: `c2a7230606c9f3be39de400fae0f5cde62ee5b0e` ([base #9](https://github.com/ScholarWorkflow/base-skills/pull/9)). The flake source selects that candidate; after upstream merge, restore the source to main and generate its lock. CI no longer clones this private repository, so its shared tests skip there and run locally and in the Nix package check.

The flake lock now records the pinned base input, and the public Nix package was evaluated, built and executed on aarch64-darwin; details are in test-results.md. Required remaining evidence: upstream integration and Linux Nix evaluation.

The updated fast-review criteria allow one bounded independent review after this evidence exists. No downstream full-business regression, source certification, blanket matrix, approval or merge is claimed or requested here.

## Follow-up bounded review, 2026-11-04

Independent review of the complete candidate and its call chains found that legacy `PDFX_VISION_CHAIN` model strings could select a shared deployment declaring `structured:false`. The correction resolves legacy selectors to shared deployment names before capability filtering, and preserves unlisted model identity. The reviewer statically rechecked this correction and its two targeted cases. The remaining Python runtime observations are in test-results.md.

Test isolation was corrected without deleting assertions: the old PATH glance compatibility case explicitly represents an installation without the optional shared runtime; the new runtime-default test retains actual subprocess/HTTP coverage. Environment keys loaded from fixture files are restored, and the existing runtime-boundary test restores its importlib/subprocess mocks. The dedicated-layout fixture writes its configuration before importing modules that read configuration.

The GitHub rulesets endpoint returned an empty list, and `branches/main/protection` now returns HTTP404 `Branch not protected` on 2026-11-04. No bypass was attempted.
