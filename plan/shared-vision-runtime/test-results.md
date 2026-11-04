# Local validation observations

Core implementation is based on `0d2348a1438edba24f966b86ab41c16bc4015edd`. Shared runtime source is base-skills `main` at `665dc0c1ed0ee25fb26062f101c69d9f51079e95` (PR #9 merged). These are execution observations, not an author-awarded approval.

## Final Python observations

- Repository command: `uv run --with pytest,pymupdf --with <checkout of the fixed base commit>/.apm/skills/vision-tools pytest -q lib/pdfx .apm/skills/formula-repair/tests .apm/skills/llm-ocr-refresh/tests test`. Result on `569e68e`/`4988ad8` (product code unchanged between them): 109 passed and 11 subtests passed. All eight shared-runtime tests are included; no remote model or credentials are used.
- Without the optional shared runtime installed, the same command reports 101 passed, 8 skipped and 11 subtests passed. The eight skips are the shared-runtime module, which requires `vision_client`; `pytest.importorskip` covers the non-Nix installation case.
- Built the staged `scholar-workflow-pdfx` wheel and the `agent-vision-runtime` wheel. Installed both in a fresh temporary virtual environment with their declared dependencies.
- Outside both checkouts, with PYTHONPATH unset: the existing `check_release_install.py` completed, `pdfx ocr-image --help` completed, and all eight shared-runtime tests passed against installed modules. This includes a real core subprocess calling only the loopback HTTP fixture and receiving model metadata.
- `git diff --check` completed without whitespace errors.

## Darwin Nix observations, 2026-11-04

Local machine: `aarch64-darwin`, Nix 2.34.8. `flake.lock` records the base input at merged `main` commit `665dc0c1ed0ee25fb26062f101c69d9f51079e95`; `nix flake check --no-build` passes for `aarch64-darwin`.

- `nix build .#packages.aarch64-darwin.pdfx --no-link` succeeded; built `/nix/store/db5r4mlrcvljjhmncad4lkpv1rrrs7lp-pdf-processing-core-0.1.0`.
- The built package ships `bin/pdfx`, `bin/pdfx-formula-repair-runner` and `bin/pdfx-ocr-refresh-jobs`; its Python environment contains `vision_client.py` and `pdfx/ocr_image.py`.
- The shipped client is the merged version: it contains gzip response decoding (6 occurrences) and one `stream` field, neither of which existed at the earlier pin.
- With `PYTHONPATH` unset, the built `pdfx ocr-image` read a local `VISION_ENV_FILE` and returned `fixture-ocr-text` from a loopback OpenAI-compatible fixture.
- A second loopback fixture returned a gzip-encoded body and recorded the request payload: `pdfx ocr-image` returned `gzip-ok stream-false`, so both the decoding and the non-streaming payload request work through core.
- A `pdfx-ocr-refresh-jobs --help` run and the core subprocess path also completed.
- This is a Darwin build and run of the package. Its earlier statement that Linux evaluation was unverified was superseded on 2026-11-05; see the platform section below.

## CI workflow correction

The prior CI step cloned base-skills for the `agent-vision-runtime` requirement. That repository is private and the public runner has no credential for it, so the run failed during dependency resolution without executing tests. The step now runs the repository suite without the cross-repository requirement; shared-runtime tests skip there and are covered locally and by the Nix package check.

- That step command was executed locally with the surrounding environment cleared on the `9355a21` tree: 100 passed, 8 skipped, 11 subtests passed. On `569e68e` and `4988ad8` it reports 101 passed and 8 skipped.
- The remaining CI steps (root build, staged release build, wheel and sdist install checks, wheel-environment skill tests, `check_skill_runtime.py`) were run locally against the same tree and all completed without error.

## Retained failure classification

The old interrupted test attempt only logged package building; no result is inferred. The first newly authorized run had 51 passes and one failed legacy PATH expectation, now scoped to the actual no-runtime compatibility case. The first aggregate run exposed pre-existing test pollution of global importlib/subprocess methods; these mocks now restore cleanly. The first isolated-wheel run exposed fixture import-before-config ordering, corrected by writing fixture configuration before module imports. No assertion was removed to produce the final successful observations.

## Refresh fallback in a real old installation, 2026-11-04

The published `scholar-workflow-pdfx 0.1.0` wheel from PyPI (uploaded 2026-09-07) contains no `pdfx/ocr_image.py`, no `vision_client`, and no `ocr-image` subcommand. Installing the merged runtime next to it yields `vision_client: True` and `pdfx.ocr_image: False`.

- In that environment the previous branch (`vision_client` absent) raised `ImportError: cannot import name 'ocr_image' from 'pdfx'`; a PATH glance was never consulted.
- The corrected branch returns the PATH glance for the same environment.
- The repository test now models exactly this installation, and `test_missing_core_module_and_glance_reports_glance_missing` covers the no-module, no-glance case by asserting the `glance_missing` code.

## Linux platform evaluation, 2026-11-05

The repin to merged base made `nix flake check --all-systems --no-build` fail: base-skills narrowed `agent-vision-runtime` to `aarch64-darwin` (`f513472`), while core exported `x86_64-linux` and injected the runtime unconditionally. The failure was `Refusing to evaluate package 'python3.14-agent-vision-runtime-0.1.0' … not available on the requested hostPlatform`. Before this PR the Linux output evaluated normally.

The factory now injects `visionRuntime` only when `pkgs.stdenv.hostPlatform.isDarwin`.

- After the correction, `nix flake check --all-systems --no-build` passes and evaluates `packages.x86_64-linux.pdfx`, `packages.x86_64-linux.pdfx-agent`, `devShells.x86_64-linux.default`, and the three Darwin outputs.
- `nix eval --raw .#packages.x86_64-linux.pdfx.drvPath` returns a derivation, and its derivation carries no vision-runtime dependency.
- The Darwin package was rebuilt at `/nix/store/0jh688p5fyf5lvyqz043dyc2sws9gy45-pdf-processing-core-0.1.0`; its environment still contains `vision_client.py` (gzip decoding present) and `pdfx/ocr_image.py`, and the gzip fixture still returns `gzip-ok stream-false`.
- Linux evaluation is established; a Linux build and a Linux runtime run are not.

## Linking the measurements to a specific base checkout

Each aggregate result is tied to its core commit:

- `49dec88`: 108 passed, 11 subtests passed with a detached checkout of `665dc0c1ed0ee25fb26062f101c69d9f51079e95` (matching the lock). An earlier 108-test result used a working tree whose content can no longer be attributed to one pinned revision; it is kept as a historical observation only.
- `569e68e` and `4988ad8`: 109 passed, 11 subtests passed with the same merged checkout, plus 101 passed and 8 skipped without the runtime (the CI command). `4988ad8` changed only documents, plan records and one test's environment isolation, so the result carries over.

The count moved from 108 to 109 because `569e68e` added the `glance_missing` case. `lib/` did not change between those commits.

## Still incomplete

The public Nix package was built and run only on `aarch64-darwin`; Linux is evaluated but not built. On Linux the package has no shared `VISION_*` path because the base runtime declares Darwin only, so only the pre-existing `PDFX_*` endpoints apply there. base-skills is still a private repository, so `nix build` requires credentials even though the upstream commit is merged. Real OCR quality, downstream workflow regression, final merge approval and consumer readiness are not claimed.
