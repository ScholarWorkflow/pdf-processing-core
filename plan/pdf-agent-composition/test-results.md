# PDF composition execution evidence

- Product: `7ac7bca10206c5481f47ccd89617566cf8a5a065` (PR #11).
- Recipe: PDF-COMPOSE-T4, published in `c389e07bd7b974b59d7f680c7627a21d901b984e`.
- Environment: cloud x86_64-linux, existing Nix 2.35.3 via its existing `bin/run` store namespace. No user Mac or external model/credential service used.
- Independent gates: see [test-review-record.md](test-review-record.md). This author record reports observations, not self-awarded PASS.

## First attempt retained

T3 stopped with exit 1 before package build/apply. The locked `agent-env-nix` revision `760c1bf60e8ec19d1985660ec5e3276a8ef80b5a` archive returned HTTP 404. Target 1 was blocked/unjudgeable, not a product assertion failure. Original log: `/tmp/pdf-compose-approved-recipe.log`.

## Reviewed same-content recovery

The connected GitHub source reader returned the exact 24-file tree for that locked revision. Its reconstructed source hash was `sha256-x1of5F3pJ5pc6V3ybv1mVfkLZ6h5rXrla3Ynlq7x2Mw=`, identical to the original lock. T4 independently reviewed only this retrieval adjustment. The existing source cache was populated, without changing source URL, revision, lock, credentials, or visibility. This does not demonstrate anonymous cold-fetch availability or restoration of the archive endpoint.

## Single T4 continuation observations

- Same run directory: `/tmp/pdf-compose-iWkpcy6c`.
- `bash /tmp/pdf-compose-T4-recipe.sh` with pipefail and a separate retained log exited 0.
- Default/override harness metadata assertion printed `true`: default `["codex"]`, explicit override `[]`.
- Existing `mkProject` built with two independently obtained instances from the same public PDF factory and identical inputs/options.
- Output: `/nix/store/4k7d310nfd80zp7dw08k3vv27li8sxzx-agent-environment`.
- Actual project application printed `agent-env: Applied project agent environment.`
- Manifest uniqueness assertion printed `true`: two skills (`formula-repair`, `llm-ocr-refresh`) and three native agents (`formula-repair`, `llm-ocr-refresh-unit-worker`, `pdf-to-text`).
- Both complete installed skill directories matched their manifest source directories with `diff -qr` exit 0; required scripts/benchmark resources existed.
- All three installed native role TOML files parsed with expected names and nonempty description/instructions. Their full instruction text is retained in the T4 log. The installed pdf-to-text instruction uses the caller's absolute fixed wrapper directly, retains normal summary behavior, and only requires JSON when the existing role contract requires it.
- Unchanged wrapper help coverage is reused from baseline PR #10 / `plan/issue-9-nix.md` §5 as identified in T4; wrappers and their dependencies were not modified. No duplicate wrapper/business regression was run.
- APM sources and consumer-owned business algorithms were untouched; code review R2 covers that observation.

This proves only the recorded producer composition/application observations. It is not a real Contact + Study dual-root result, an Exam native-role handoff result, a Darwin run, or a merge approval. Exam #34 owns its changed caller handoff. Final top-level combination is recorded once by Study #16, cited by Contact #131.

## Retained local evidence

- T3 log SHA256: `e31d9dc7b6bb4ae62eb344f8366284876e042e928f2eec69e3e36816e87c4518`.
- T4 log `/tmp/pdf-compose-T4-recipe.log` SHA256: `1be90389b2c0e3543a34621e09eb51bd627588fd365c328b2e20d11b50ca9ef4`.
