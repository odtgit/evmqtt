# Releasing

1. Bump `version` in `pyproject.toml` and `config.yaml` to the same value (no `v` prefix).
2. On the first release that has GHCR images available, in the same commit add to `config.yaml`:
   ```yaml
   image: ghcr.io/odtgit/evmqtt
   ```
   Do not use an `{arch}` placeholder: CI publishes one multi-arch manifest list per
   tag (amd64 + aarch64), so a single repo name is correct. Supervisor pulls
   `image:version` using the exact `version` field, never `latest` or `edge`.
3. Merge to master, then create a GitHub release with tag `vX.Y.Z` matching the version.
4. CI (`.github/workflows/release.yml` and `.github/workflows/ci.yml`) then:
   - builds sdist/wheel, runs `twine check`, checks the version against the tag, and
     publishes to PyPI via Trusted Publishing.
   - builds and pushes multi-arch images to `ghcr.io/odtgit/evmqtt` tagged with the
     semver version and `latest`.
5. Verify:
   - `pip index versions evmqtt` or check the PyPI project page shows the new version.
   - `docker buildx imagetools inspect ghcr.io/odtgit/evmqtt:X.Y.Z` shows both
     `linux/amd64` and `linux/aarch64`.
   - In Home Assistant, reload the add-on repository and confirm the update is offered.

## One-time setup (owner, on pypi.org)

Create a "pending publisher" at https://pypi.org/manage/account/publishing/ with:

- PyPI project name: `evmqtt`
- Owner: `odtgit`
- Repository name: `evmqtt`
- Workflow name: `release.yml`
- Environment name: `pypi`

## One-time setup (owner, on GitHub)

Create a GitHub Actions environment named `pypi` in the repo settings
(Settings > Environments). No secrets are needed; Trusted Publishing uses OIDC.
Optionally add required reviewers to gate publishing.

For the optional TestPyPI path (`workflow_dispatch`), repeat both steps above with
an `environment: testpypi` and a matching pending publisher on test.pypi.org.
