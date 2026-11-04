# Shared vision runtime integration

## Scope

PDF OCR configuration and transport belong to core plus the base shared vision client. Consumers retain their page selection, caches, filenames and workflow outputs. This PR integrates the base `agent-vision-runtime` package; it does not migrate downstream repositories.

1. Inject `base-skills.lib.mkVisionRuntime { pkgs; python = pkgs.python3; }` into the public PDF package factory. Pin the actual base commit in the lock. Retain the optional low-level import argument for existing private-import consumers; migrate those consumers separately to the public factory.
2. Load base's existing local configuration once per process. Preserve its documented precedence and budget/protocol/authentication behavior. Explicit `PDFX_<ROLE>_ENDPOINT`/`PDFX_VISION_ENDPOINT` retain the existing complete-URL OpenAI-compatible path. Shared `VISION_*` uses base's client. Model overrides resolve at invocation.
3. Preserve original rendered bytes, task prompts, token limits and structured chain filtering. Keep multipart layout-box endpoints separate from image chat APIs; retain text/native extraction and cache/audit behavior introduced by #14.
4. Export `pdfx ocr-image IMAGE` and make refresh's default subprocess the installed core OCR module. Preserve explicit `--glance`/`OCR_REFRESH_GLANCE`, plus old non-Nix PATH fallback when shared runtime is absent. Non-Nix text/native/PDFX endpoint use stays independent of the optional new runtime.
5. Perform the focused checks in test-plan.md and independent fast-review assessment against the final base/core commits. No merge is authorized by this PR.

## Integration prerequisite

The base runtime branch must be merged and available as a fixed upstream input before the core Nix package is ready. base-skills #9 merged as `665dc0c1ed0ee25fb26062f101c69d9f51079e95`; the flake source now points at base-skills `main` and the lock records that commit. The base-skills repository remains private, so evaluation requires credentials. Nix builds and actual external model quality are not implied by Python tests.
