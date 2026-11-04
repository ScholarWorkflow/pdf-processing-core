# Candidate review and validation record

Base: `0d2348a1438edba24f966b86ab41c16bc4015edd` (merged #14).

Independent read-only review examined the complete candidate diff and extraction, TOC, audit, refresh and shared-client call chains. Two concrete issues were corrected and rechecked statically:

- Role-model overrides could restore a structured:false deployment when several deployments share a model name. Resolve the override then preserve the selected deployment name; a parameterized targeted test covers the combination.
- A shared generic model could accept the dedicated layout adapter's empty prompt and return a description instead of OCR. Without an explicit legacy endpoint or PDFX_LAYOUT_MODEL, that adapter is unavailable and the existing extraction chain falls through to the transcription prompt. The targeted adapter check preserves the exact prompt; extraction fallback was also inspected.

Status: NOT_READY pending upstream integration and final merge-constraint verification. The user explicitly authorized local dependency installation, tests and simulated HTTP services on 2026-11-04. Python observations are recorded in test-results.md; they are not a final independent approval. No external service/model was called.

Base runtime: [base #9](https://github.com/ScholarWorkflow/base-skills/pull/9) merged to `main` as `665dc0c1ed0ee25fb26062f101c69d9f51079e95`. The flake source now points at base-skills `main` and the lock records that commit. CI no longer clones this private repository, so its shared tests skip there and run locally and in the Nix package check.

The flake lock now records the pinned base input, and the public Nix package was evaluated, built and executed on aarch64-darwin; details are in test-results.md. Required remaining evidence: upstream integration and Linux Nix evaluation.

The updated fast-review criteria allow one bounded independent review after this evidence exists. No downstream full-business regression, source certification, blanket matrix, approval or merge is claimed or requested here.

## Post-merge repin and fallback correction, 2026-11-04

base-skills #9 merged; the earlier pinned commit `c2a7230` predated two follow-up commits that restored gzip response decoding, `stream: false`, and non-retrying daily-quota 429 handling, and corrected chain metadata. The flake input now follows base-skills `main` and `flake.lock` records `665dc0c1ed0ee25fb26062f101c69d9f51079e95`. The public package was rebuilt, and its environment now contains the gzip/stream handling; a loopback fixture returning a gzip-encoded body with `stream: false` was decoded through `pdfx ocr-image`.

`default_glance_path()` previously branched on `vision_client` being absent. The real compatibility condition is whether the installed core ships `pdfx.ocr_image`: the published `scholar-workflow-pdfx 0.1.0` wheel has neither that module nor `ocr-image`, so installing only the shared runtime still failed. The check now tests `pdfx.ocr_image` and raises `glance_missing` when neither the module nor a PATH glance exists. The PATH-selection case was corrected to model the actual no-module installation, and a second case covers a shared-runtime-only installation. Both were confirmed against the published wheel in an isolated environment: the old branch raised ImportError there, the new branch returns the PATH glance.

## Follow-up bounded review, 2026-11-04

Independent review of the complete candidate and its call chains found that legacy `PDFX_VISION_CHAIN` model strings could select a shared deployment declaring `structured:false`. The correction resolves legacy selectors to shared deployment names before capability filtering, and preserves unlisted model identity. The reviewer statically rechecked this correction and its two targeted cases. The remaining Python runtime observations are in test-results.md.

Test isolation was corrected without deleting assertions: the old PATH glance compatibility case explicitly represents an installation without the optional shared runtime; the new runtime-default test retains actual subprocess/HTTP coverage. Environment keys loaded from fixture files are restored, and the existing runtime-boundary test restores its importlib/subprocess mocks. The dedicated-layout fixture writes its configuration before importing modules that read configuration.

The GitHub rulesets endpoint returned an empty list, and `branches/main/protection` now returns HTTP404 `Branch not protected` on 2026-11-04. No bypass was attempted.
