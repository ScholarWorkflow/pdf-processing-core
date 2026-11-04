# Local validation observations

2026-11-04, cloud x86_64-linux, CPython 3.12.14. Core implementation is based on `0d2348a1438edba24f966b86ab41c16bc4015edd`; shared runtime source is base-skills PR9 `c2a7230606c9f3be39de400fae0f5cde62ee5b0e`. These are execution observations, not an author-awarded approval or Nix/Darwin result.

## Final Python observations

- Repository command: `uv run --with pytest,pymupdf --with <base-checkout>/.apm/skills/vision-tools pytest -q lib/pdfx .apm/skills/formula-repair/tests .apm/skills/llm-ocr-refresh/tests test`. Result: 108 passed and 11 subtests passed. All eight shared-runtime tests are included; no remote model or credentials are used.
- Built the staged `scholar-workflow-pdfx` wheel and the `agent-vision-runtime` wheel. Installed both in a fresh temporary virtual environment with their declared dependencies.
- Outside both checkouts, with PYTHONPATH unset: the existing `check_release_install.py` completed, `pdfx ocr-image --help` completed, and all eight shared-runtime tests passed against installed modules. This includes a real core subprocess calling only the loopback HTTP fixture and receiving model metadata.
- `git diff --check` completed without whitespace errors.

## Retained failure classification

The old interrupted test attempt only logged package building; no result is inferred. The first newly authorized run had 51 passes and one failed legacy PATH expectation, now scoped to the actual no-runtime compatibility case. The first aggregate run exposed pre-existing test pollution of global importlib/subprocess methods; these mocks now restore cleanly. The first isolated-wheel run exposed fixture import-before-config ordering, corrected by writing fixture configuration before module imports. No assertion was removed to produce the final successful observations.

## Still incomplete

The cloud environment has no Nix executable. The candidate flake selects the exact base PR9 revision but `flake.lock` does not yet contain the new input. After base is merged, restore its source to main, generate the lock normally, and validate the actual public Nix package. No Linux Nix build, Darwin execution, real OCR quality, downstream workflow regression, final merge approval or consumer readiness is claimed.
