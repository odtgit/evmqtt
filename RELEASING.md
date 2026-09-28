# Releasing

Releases are automatic. Every push to `master` that contains a releasable
commit is versioned, tagged, published to PyPI, and its multi-arch image is
pushed to GHCR, with no manual tagging or GitHub release step.

## How it works

1. A PR merges to `master` (squash merge only, PR title becomes the commit
   subject).
2. `.github/workflows/release.yml` runs on every push to `master`:
   - `version`: reads commit subjects (and BREAKING CHANGE footers) since the
     last `v*` tag and computes the next version with `scripts/release.py
     next-version`. If nothing releasable happened, the workflow stops here.
   - `tag`: runs `scripts/release.py apply X.Y.Z` (bumps `pyproject.toml`,
     `config.yaml`, `custom_components/evmqtt/manifest.json` if present, and
     prepends `CHANGELOG.md`), commits as `release: X.Y.Z [skip ci]`, pushes
     to `master`, tags `vX.Y.Z`, and creates the GitHub release.
   - `build` + `publish-pypi`: build sdist/wheel, `twine check`, verify the
     version against the tag, publish via PyPI Trusted Publishing.
   - `images`: build and push the multi-arch image to
     `ghcr.io/odtgit/evmqtt` tagged `X.Y.Z` and `latest`.
3. Pushes and releases made with the workflow's `GITHUB_TOKEN` don't trigger
   other workflow runs, so there's no loop: `ci.yml` does not re-run on the
   `release:` commit or on the tag push. `ci.yml`'s own push-to-master run
   (for the human commit that got merged) still builds and pushes the `edge`
   image as usual, from code identical to what gets released moments later.
   The one gap: `edge`'s baked-in version string (`evmqtt --version`, the
   `io.hass.version` label) is stamped from `pyproject.toml` at build time,
   which for that `edge` build is still the pre-bump version, since the bump
   commit hasn't landed yet. `edge` is functionally current but reports a
   version one release behind until the next push to `master` rebuilds it.
   Nothing consumes `edge`'s version string for anything besides display, so
   this doesn't affect correctness.

## Prefix-to-bump table

| Commit type (PR title prefix) | Bump    |
| ------------------------------ | ------- |
| `<type>!:` or `BREAKING CHANGE:` footer | major |
| `feat:`                        | minor   |
| `fix:`, `perf:`                | patch   |
| `docs:`, `chore:`, `ci:`, `test:`, `refactor:`, `style:`, `build:`, `release:` | none (no release) |

For a squash-merged PR the commit subject is the PR title, so the PR title's
prefix decides the bump. `.github/workflows/pr-title-lint.yml` enforces the
type list at PR time.

## Shipping nothing

Use a non-releasing type (`docs:`, `chore:`, `ci:`, `test:`, `refactor:`,
`style:`, `build:`) for the PR title. The `version` job computes no release
and every downstream job is skipped.

## Manual runs (`workflow_dispatch`)

- **Force a bump**: run the workflow with `bump` set to `patch`, `minor`, or
  `major` (instead of `auto`) to release regardless of what the merged
  commits say.
- **Re-publish an existing tag**: run the workflow with `republish_tag` set
  to `vX.Y.Z`. This skips computing a new version and re-runs build + image
  + PyPI publish for that tag, without creating a new commit or tag.

## Recovering from a failed publish

The `tag` job creates the git tag and GitHub release before `build` and
`publish-pypi` run. If PyPI publishing or the image push fails after the tag
exists, don't re-run `version`/`tag` (the tag is already there). Instead, use
`workflow_dispatch` with `republish_tag: vX.Y.Z` to retry `build`, `images`
and `publish-pypi` for that tag.

## Verify

- `pip index versions evmqtt` or the PyPI project page shows the new version.
- `docker buildx imagetools inspect ghcr.io/odtgit/evmqtt:X.Y.Z` shows both
  `linux/amd64` and `linux/aarch64`.
- In Home Assistant, reload the add-on repository and confirm the update is
  offered.

## Safety: what the bot commit can touch

The `tag` job's `release: X.Y.Z` commit is only allowed to change
`pyproject.toml`, `config.yaml`, `CHANGELOG.md` and
`custom_components/evmqtt/manifest.json`. Before committing, the job diffs
the staged changes against that allow-list and fails the workflow if
anything else changed, so a compromised or buggy `apply` step can't use the
bot's push rights to ship arbitrary code to `master`.

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

### Branch protection / ruleset bypass for the release bot

`master` is protected by a ruleset that requires PRs and a passing
security-review check. The `tag` job pushes `release: X.Y.Z` directly to
`master` using the workflow's own `GITHUB_TOKEN` (identity
`github-actions[bot]`), which must be allowed to bypass that PR requirement
for this specific, narrow, scope-checked commit.

GitHub rulesets support a bypass actor of type `Integration` for the
built-in GitHub Actions app (app id `15368`, slug `github-actions`), which
matches the identity `GITHUB_TOKEN` pushes commit as. Configure it once via
the API (no UI option lists it explicitly as of writing):

```bash
# find the ruleset id
gh api repos/odtgit/evmqtt/rulesets

# add the bypass actor, preserving the ruleset's existing fields
gh api repos/odtgit/evmqtt/rulesets/<ruleset_id> -X PUT \
  -f "bypass_actors[][actor_id]=15368" \
  -f "bypass_actors[][actor_type]=Integration" \
  -f "bypass_actors[][bypass_mode]=always" \
  # ...plus every other field already on the ruleset (name, target,
  # enforcement, conditions, rules) since PUT replaces the object
```

In practice it's easier to `gh api repos/odtgit/evmqtt/rulesets/<id>` to
fetch the current JSON, add the bypass actor entry above to its
`bypass_actors` array, and `PUT` the whole document back.

This bypass is scoped to this one ruleset and only lets the Actions app push
past the PR requirement; it does not grant it admin or bypass the
security-review status check on PRs (PRs still go through the normal gate).
The diff-scope guard in the `tag` job (see "Safety" above) is what stops
this bypass from being usable to ship arbitrary code, since the bypass
actor alone would otherwise let any workflow on `master` push unreviewed
commits.
