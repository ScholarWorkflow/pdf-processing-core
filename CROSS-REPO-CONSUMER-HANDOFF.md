# Cross-Repository Consumer Handoff

The preferred distribution for the public `pdfx` runtime is now:

```text
scholar-workflow-pdfx>=0.1.0,<0.2
```

It provides the stable public surface:

```python
import pdfx
```

and the `pdfx` console command. The release project is staged from the
canonical `lib/pdfx` source tree, so this repository does not maintain a
second copy of the implementation.

## Existing downstream blocker

`ScholarWorkflow/paper-analysis` currently depends on:

```text
pdf-processing-core
```

from this Git repository. This issue does not modify that repository. Until
its migration lands:

- keep the root `pdf-processing-core` project metadata compatible;
- keep the existing Git-consumer path working;
- do not remove legacy distribution-name compatibility.

The downstream repository must migrate to
`scholar-workflow-pdfx>=0.1.0,<0.2` in its own change. After that migration is
merged, use a separate cleanup issue to remove the legacy root distribution
name from this repository.

## Required future replacements

- Direct implementation-module execution -> `uvx --from
  'scholar-workflow-pdfx>=0.1.0,<0.2' pdfx ...`
- Direct status-script execution -> the public `pdfx status ...` command when
  that command is adopted by the consumer workflow
- `sys.path` injection for `pdfx` imports -> the bounded
  `scholar-workflow-pdfx` dependency and `import pdfx`

Consumers must supply exact source-document, paired-PDF, auxiliary-source,
and caller-metadata paths to public commands. Fingerprints, state files,
result files, and atomic finalize markers remain runtime contracts rather than
implementation details.
