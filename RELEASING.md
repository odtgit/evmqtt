# Releasing

Releases are automatic. Every push to `master` that contains a releasable
commit is versioned, tagged, published to PyPI, and its multi-arch image is
pushed to GHCR, with no manual tagging or GitHub release step.

## How it works

1. A PR merges to `master` (squash merge only, PR title becomes the commit
   subject).
2. `.github/workflows/release.yml` runs on every push to `master`:
   - `version` (read-only): reads commit subjects (and BREAKING CHANGE
     footers) since the last numeric `vX.Y.Z` tag and computes the next
     version with `scripts/release.py next-version`. If nothing releasable
     happened, the workflow stops here.
   - `prepare` (read-only): runs `scripts/release.py apply X.Y.Z` (bumps
     `pyproject.toml`, `config.yaml`, `custom_components/evmqtt/manifest.json`
     if present, and prepends `CHANGELOG.md`) against a plain checkout with no
     push credentials, and uploads the resulting `git diff` and release notes
     as a build artifact. Nothing is committed here.
   - `validate-and-push` (holds the push credential): checks out `master`
     with `persist-credentials: false` and an SSH deploy key instead of
     `GITHUB_TOKEN`, downloads the artifact, and re-validates the raw diff
     text with plain shell (not by running any repo script) before applying
     it: only `pyproject.toml`'s `version =`, `config.yaml`'s `version:`,
     the manifest's `"version"`/`evmqtt==` pin, and pure additions to the top
     of `CHANGELOG.md` may appear. It then commits as the release identity
     (`evmqtt-release[bot]`, see below), pushes to `master`, tags `vX.Y.Z`,
     and creates the GitHub release. `GITHUB_TOKEN` is used for exactly one
     step here (`gh release create`); everything else uses the deploy key.
   - `build` + `publish-pypi`: build sdist/wheel, `twine check`, verify the
     version against the tag, publish via PyPI Trusted Publishing.
   - `images`: build and push the multi-arch image to
     `ghcr.io/odtgit/evmqtt` tagged `X.Y.Z` and `latest`.
3. Pushes and releases made with `GITHUB_TOKEN` don't trigger other workflow
   runs, and the deploy-key push isn't `GITHUB_TOKEN` either, so there's no
   loop: `ci.yml` does not re-run on the `release:` commit or the tag push.
   `ci.yml`'s own push-to-master run (for the human commit that got merged)
   still builds and pushes the `edge` image as usual, from code identical to
   what gets released moments later. The one gap: `edge`'s baked-in version
   string (`evmqtt --version`, the `io.hass.version` label) is stamped from
   `pyproject.toml` at build time, which for that `edge` build is still the
   pre-bump version, since the bump commit hasn't landed yet. `edge` is
   functionally current but reports a version one release behind until the
   next push to `master` rebuilds it. Nothing consumes `edge`'s version
   string for anything besides display, so this doesn't affect correctness.

## Prefix-to-bump table

| Commit type (PR title prefix) | Bump    |
| ------------------------------ | ------- |
| `<type>!:` or `BREAKING CHANGE:` footer | major |
| `feat:`                        | minor   |
| `fix:`, `perf:`                | patch   |
| `docs:`, `chore:`, `ci:`, `test:`, `refactor:`, `style:`, `build:`, `release:` | none (no release) |

For a squash-merged PR the commit subject is the PR title, so the PR title's
prefix decides the bump. `.github/workflows/pr-title-lint.yml` enforces the
type list at PR time. Non-numeric tags (`v1.2.0-rc1`, etc.) are ignored when
looking for the last release; only `vX.Y.Z` counts.

## Shipping nothing

Use a non-releasing type (`docs:`, `chore:`, `ci:`, `test:`, `refactor:`,
`style:`, `build:`) for the PR title. The `version` job computes no release
and every downstream job is skipped.

## Manual runs (`workflow_dispatch`)

- **Force a bump**: run the workflow with `bump` set to `patch`, `minor`, or
  `major` (instead of `auto`) to release regardless of what the merged
  commits say. This still goes through `prepare`/`validate-and-push` like a
  normal release.
- **Re-publish an existing tag**: run the workflow with `republish_tag` set
  to `vX.Y.Z`. This only rebuilds and republishes; it never commits, tags, or
  moves `:latest`. Before doing anything it requires: the tag is an ancestor
  of `origin/master`, and the tagged commit is a `release: X.Y.Z [skip ci]`
  commit authored by the release identity matching that exact version - so
  it can't be pointed at an arbitrary or hand-made tag. If the version's
  image tag already exists in GHCR, republish refuses to overwrite it unless
  you also set `force_overwrite_image: true`.

## Recovering from a failure

- **`master` moved while `version`/`prepare` were computing the diff**: the
  `validate-and-push` job re-checks the base commit right before applying
  the patch and fails the run if it no longer matches, rather than
  reapplying a diff computed against a stale tip (which could recompute the
  wrong version or a stale changelog). Chosen deliberately over a
  rebase-and-retry: the next push to `master` reruns `version` from the new
  tip and nothing is lost, it just takes one more push's worth of latency.
- **The release commit pushed but the tag push failed**: on the next run,
  `version` detects that `master`'s tip is already an untagged
  `release: X.Y.Z [skip ci]` commit by the release identity
  (`scripts/release.py pending-retag`) and the workflow only re-tags and
  creates the release; it does not recompute or reapply anything.
- **The tag exists but `gh release create` failed**: `validate-and-push`
  itself is idempotent here (`gh release view` before `gh release create`),
  so simply re-running the workflow (push a trivial no-op, or use
  `workflow_dispatch` with `bump: auto` once there's a new commit) will pick
  it up via the untagged-commit/already-tagged paths as applicable. If
  `master`'s tip has moved past the release commit by the time you notice,
  there's no automatic path to backfill a missing GitHub Release page for an
  old tag; create it by hand (`gh release create vX.Y.Z --notes-file ...`)
  referencing the existing tag - `build`/`images`/`publish-pypi` for that
  version can still be replayed via `republish_tag`.
- **PyPI or the image push failed after the tag and release exist**: use
  `workflow_dispatch` with `republish_tag: vX.Y.Z` to retry `build`, `images`
  and `publish-pypi` for that tag.

## Verify

- `pip index versions evmqtt` or the PyPI project page shows the new version.
- `docker buildx imagetools inspect ghcr.io/odtgit/evmqtt:X.Y.Z` shows both
  `linux/amd64` and `linux/aarch64`.
- In Home Assistant, reload the add-on repository and confirm the update is
  offered.

## Safety: what the release commit can touch

`validate-and-push` is the only job with push credentials, and it never runs
`scripts/release.py` or any other repo-authored code - it downloads the diff
that `prepare` (a read-only job, no credentials) produced, and validates the
*raw diff text* with inline shell before applying it:

- the changed files are a subset of `pyproject.toml`, `config.yaml`,
  `CHANGELOG.md`, `custom_components/evmqtt/manifest.json`;
- every changed line in the first three mentions `version` or `evmqtt==`,
  and normalising away `X.Y.Z`-shaped numbers makes the added and removed
  line sets identical - i.e. only version digits changed, nothing else about
  the line did;
- `CHANGELOG.md`'s diff contains no removed lines at all (pure prepend).

This means even a compromised or buggy `release.py` (which runs in the
read-only `prepare` job) can, at worst, produce a patch that gets rejected;
it can't use the push credential to ship arbitrary code, because the job
holding that credential never executes what `prepare` produced beyond
`git apply`ing a diff it already independently verified is in-scope.

## Release identity

Commits and tags made by the release automation are attributed to
`evmqtt-release[bot] <evmqtt-release-bot@users.noreply.github.com>` (see
`RELEASE_IDENTITY_NAME`/`RELEASE_IDENTITY_EMAIL` in `scripts/release.py`).
This is not a GitHub user or App; it's just the git commit identity used
consistently so `republish_tag` and the untagged-commit recovery can verify
a tag or commit really came from this automation before trusting it.

## One-time setup (owner, on pypi.org)

Create a "pending publisher" at https://pypi.org/manage/account/publishing/ with:

- PyPI project name: `evmqtt`
- Owner: `odtgit`
- Repository name: `evmqtt`
- Workflow name: `release.yml`
- Environment name: `pypi`

## One-time setup (owner, on GitHub)

Create a GitHub Actions environment named `pypi` in the repo settings
(Settings > Environments), allowing branch `master` and tags `v*`. No
secrets are needed; Trusted Publishing uses OIDC. Optionally add required
reviewers to gate publishing.

For the optional TestPyPI path (`workflow_dispatch`), repeat both steps
above with an `environment: testpypi` and a matching pending publisher on
test.pypi.org.

### Release deploy key (push credential for `validate-and-push`)

`master` is protected by a ruleset that requires PRs and a passing
security-review check, and tags matching `v*` are protected by
`scripts/setup_tag_ruleset.sh` against creation/update/deletion. The
`validate-and-push` job needs to push past both, for exactly the
narrow, scope-checked commits described above.

This deliberately does **not** use a ruleset bypass for the built-in
GitHub Actions app (`Integration` actor, app id `15368`): that bypass
would apply to the identity every workflow's default `GITHUB_TOKEN` pushes
as, so *any* workflow with write access - not just this one, carefully
scoped job - could push straight past the PR requirement. Instead, both the
`master` branch ruleset and the tag ruleset bypass only a dedicated SSH
deploy key, which is a credential that only exists in the `release`
environment's secret and is never a token any other workflow run holds.

One-time setup (owner):

```bash
# 1. Generate a dedicated key. No passphrase - it runs unattended in CI.
ssh-keygen -t ed25519 -C "evmqtt-release-bot" -N "" -f evmqtt-release-deploy-key

# 2. Add the public half to the repo as a deploy key with write access.
gh repo deploy-key add evmqtt-release-deploy-key.pub \
  --repo odtgit/evmqtt --title "release automation" --allow-write

# 3. Create the "release" environment (Settings > Environments), restricted
#    to branch "master", then store the private half as its secret. Only
#    the validate-and-push job in release.yml references this environment,
#    so no other job can read the secret.
gh secret set RELEASE_DEPLOY_KEY --repo odtgit/evmqtt --env release \
  < evmqtt-release-deploy-key

# 4. Delete the local private key material once it's stored.
rm evmqtt-release-deploy-key evmqtt-release-deploy-key.pub

# 5. Protect refs/tags/v* so only that deploy key can create/update/delete
#    release tags (see scripts/setup_tag_ruleset.sh for the exact payload).
scripts/setup_tag_ruleset.sh odtgit/evmqtt
```

The `master` branch ruleset itself is managed elsewhere (it also gates the
security-review status check); whoever configures its bypass actor should
use the same mechanism - a `DeployKey` bypass actor - not an `Integration`
bypass for the Actions app, and not a different key than the one stored in
the `release` environment, or `validate-and-push`'s push will be blocked
just like any other unreviewed push.

`scripts/setup_tag_ruleset.sh` does not create keys or secrets; it only
creates/updates the ruleset once the deploy key from steps 1-4 already
exists on the repo (`bypass_actors` with `actor_type: DeployKey` matches any
deploy key with write access on the repo - not a specific key id, since the
ruleset API requires `actor_id: null` for that actor type - so the repo
should only ever have the one release deploy key with write access).
