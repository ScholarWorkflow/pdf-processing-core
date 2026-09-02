# Cross-Repository Consumer Handoff

`pdf-processing-core` publishes the installable `pdfx` package and the
`pdfx` console command. Consumers may keep their compatibility entry points
until their own migration work is complete.

## Required future replacements

- Direct implementation-module execution -> `pdfx ...`
- Direct status-script execution -> `pdfx status ...` when that command is
  adopted by the consumer workflow
- `sys.path` injection for `pdfx` imports -> an installed
  `pdf-processing-core` dependency and `import pdfx`

## Preserved compatibility

- The repository-local direct CLI entry point remains self-locating.
- `lib/formula_check_cache.py` remains a thin compatibility import.
- No consumer repository is modified by this handoff.

## Consumer responsibilities

- Supply exact source-document, paired-PDF, auxiliary-source, and caller-
  metadata paths to the public commands.
- Treat fingerprints, state files, result files, and atomic finalize markers
  as runtime contracts rather than implementation details.
- Use the public package and command surface instead of importing private
  modules or depending on repository layout.
