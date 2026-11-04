# Shared OCR configuration

The `aarch64-darwin` build of the public Nix `pdfx` package includes base-skills' lightweight `agent-vision-runtime`, not the macOS browser/vision agent. Set `VISION_ENV_FILE` to a local file path in the environment that launches Codex/pdfx. Keep credentials in that file, outside the Nix store. All core OCR entry points inherit this process environment. The `x86_64-linux` build does not include the runtime, so `VISION_*` configuration has no effect there and only the `PDFX_*` endpoints apply.

The shared client reads the selected file once per pdfx process. Without `VISION_ENV_FILE`, its existing default locations and precedence apply; loaded file values override process environment values. Set a single explicit file for predictable behavior. Values are simple `KEY=VALUE`, not shell expressions. Changing the file applies to the next process.

Use the client's `VISION_BASE_URL`, `VISION_API_KEY`, `VISION_MODEL`, optional `VISION_API_PROTOCOL` or `VISION_CHAIN`. Base handles authentication, protocol payloads, fallback and budgets. Core supplies unresized rendered image bytes and task-specific prompts; it does not invoke glance's generic resize/prompt defaults.

Existing `PDFX_<ROLE>_ENDPOINT` or `PDFX_VISION_ENDPOINT` take precedence for that role and remain complete URLs using the previous OpenAI image-completion contract. Existing model overrides are preserved. `PDFX_LAYOUT_ENDPOINT` for structured box detection still requires the existing multipart `{width,height,boxes}` service: a general vision endpoint is not a substitute. A shared vision configuration alone does not add that service.

`pdfx ocr-image page.png` transcribes one rendered image. `pdfx extract` keeps text/clean/native decisions and provider order unchanged. Its dedicated layout OCR adapter requires an explicit legacy endpoint or `PDFX_LAYOUT_MODEL`; with only generic shared VISION configuration it falls through to the transcription adapter, whose OCR/LaTeX prompt is preserved. An empty dedicated-layout prompt is never silently sent to an unspecified general model. The refresh runner defaults to core's image OCR subprocess. Explicit `--glance` and `OCR_REFRESH_GLANCE` remain compatibility overrides; older pdfx distributions without the core OCR module retain PATH glance fallback, whether or not the shared runtime is installed.

Non-Nix installations may install the lightweight runtime distribution from the merged base-skills commit using its `.apm/skills/vision-tools` Python subproject. The base-skills repository is private, so this pull needs credentials:

```sh
uv pip install "agent-vision-runtime @ git+https://github.com/ScholarWorkflow/base-skills@665dc0c1ed0ee25fb26062f101c69d9f51079e95#subdirectory=.apm/skills/vision-tools"
```

This is a separate optional dependency until published: existing text/native and explicit PDFX endpoint installations continue to work without it. The runtime alone does not add core's `pdfx ocr-image`; that arrives with the next pdfx release. No unpublished PyPI availability is assumed.

Consumers must use core's public `lib.mkPdfPackages` factory to inherit its shared runtime on Darwin; the factory injects it only there. Historical direct imports of `nix/pdfx.nix` still work but require its `visionRuntime` argument to gain this capability; those migrations are separate PRs.
