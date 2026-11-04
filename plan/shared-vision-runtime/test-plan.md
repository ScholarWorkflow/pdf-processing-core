# Targeted validation

- Run `test/test_shared_vision_runtime.py` against the actual base runtime module. A local HTTP fixture verifies local env-file loading, auth, URL, exact image bytes/prompt/token limit, structured chain selection, retained legacy full endpoint and multipart contract. The refresh check invokes a real subprocess and reads its model metadata. No remote model or real credentials are used.
- Run existing core/refresh/runtime-boundary tests for affected paths, preserving #14's tests. These do not evaluate subjective OCR quality.
- Build/install the core and base wheels in an isolated environment and check the public CLI/refresh module outside the checkout.
- Evaluate and build the public Nix package against the pinned base runtime; check fixed Python import and command entry. Record platform coverage honestly. A Linux check does not establish Darwin execution.
- Independent reviewer reads complete diff, relevant callers and actual evidence; applies the updated six fast-review conditions. Missing evidence remains NOT_READY rather than inventing PASS or a larger regression matrix.

No full book, study, professor or downstream business regression is part of this change. No real paid OCR/API call is authorized or required for the local transport contract check.
