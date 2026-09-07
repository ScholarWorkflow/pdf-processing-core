# Releasing `scholar-workflow-pdfx`

The release package is built from the canonical `lib/pdfx` source tree by
`scripts/stage_release_package.py`. The repository root remains the temporary
`pdf-processing-core` compatibility project until `ScholarWorkflow/paper-analysis`
migrates in a separate change.

## Required external setup

Before the first release, configure:

- PyPI project: `scholar-workflow-pdfx`
- GitHub repository: `ScholarWorkflow/pdf-processing-core`
- GitHub Actions workflow: `.github/workflows/publish-pypi.yml`
- GitHub environment: `pypi`
- PyPI Trusted Publishing for this repository and workflow
- GitHub Actions OIDC (`id-token: write`) for the publish job

No `PYPI_TOKEN`, username/password credential, or other long-lived upload
secret is required.

## Release procedure

Create and publish a GitHub Release whose tag exactly matches the release
package version, for example `v0.1.0`. The workflow checks that tag before
building, runs the tests and clean wheel/sdist installation checks, and then
publishes the exact artifacts produced by the build job.

After `v0.1.0` is published, verify it from a clean environment:

```bash
uvx --from 'scholar-workflow-pdfx==0.1.0' pdfx --help
```

Also verify that `import pdfx` works in an environment containing only the
released distribution and its dependencies. The skill-runtime migration to
PEP 723 and `uv run --script` must not be merged until this gate is complete.
