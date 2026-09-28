# Security

## Reporting a vulnerability

Use GitHub's private vulnerability reporting (Security tab, "Report a vulnerability"). Do not open a public issue.

## Why this repository is gated

evmqtt reads raw input events on Home Assistant hosts: every key typed on a watched keyboard, passwords included. The add-on runs with host networking, full access and SYS_RAWIO. Merges to master are released automatically to PyPI, GHCR and Home Assistant users (add-on and HACS). A malicious or careless change can therefore reach many hosts with no further human step. The only control before merge is the `security-review` check described here, backed by the rulesets in `scripts/setup_ruleset.sh`.

## The gate

Workflow: `.github/workflows/security-review.yml`. Code: `.github/security-review/`.

1. **Deterministic pre-checks** (`policy.py`, no AI, run first, hard fail):
   - bidi, zero-width and other invisible characters (U+202A..U+202E, U+2066..U+206F, U+200B..U+200F, U+2060..U+2064, U+FEFF, U+2028, U+2029, U+061C, U+00AD, U+034F, Hangul fillers, U+180E and the U+E0000 tag block) in added lines, file paths, PR title, body or commit messages
   - added or changed binary files, except PNG/JPEG/GIF/ICO images whose magic bytes match and that are under 1 MB
   - size limits: more than 150 files, a diff over 200k characters, review context over 800k characters, a single file over 250k characters, or a file GitHub will not diff. The author is told to split the PR.
   - `pull_request_target` or `workflow_run` added to any workflow other than the gate, `curl|sh` style downloads in sensitive files, and any other workflow whose content mentions `security-review` at all (status spoofing)
   - files in the gate's fixture directory that are not inert `*.fx` or `meta.json`
2. **Sensitive paths** switch the AI review to strict mode instead of failing: `.github/**`, Dockerfiles, compose files, add-on `config.yaml`/`build.yaml`/`repository.yaml`, `pyproject.toml`, `setup.*`, requirements files, `custom_components/*/manifest.json`, `hacs.json`, `run.sh`, `scripts/**`, `*.service`, `.gitattributes`, `.gitmodules`, pre-commit config, Makefile/tox/nox, `conftest.py`, `sitecustomize.py`, `*.pth`, add-on `apparmor.txt`/`build.json` and this file. In strict mode the pre-change content of those files is included and the blocking threshold drops from high to medium.
3. **Two AI passes**, both with model `claude-opus-5-5` at high effort:
   - Reviewer: finds suspicious changes with file, line, category, severity, evidence and rationale.
   - Verifier: a different system prompt. It re-derives from the diff, confirms, refutes or marks each finding uncertain, and hunts for anything the reviewer missed. It is told the reviewer may have been manipulated.
4. **Verdict** (`verdict.py`), fail closed. FAIL on:
   - any pre-check failure
   - a finding at or above the threshold that the verifier did not refute. Uncertain findings keep the higher of the two severities; findings the verifier did not assess count as-is; the verifier's new findings count as confirmed.
   - any CLI or API error, timeout, refusal, malformed or schema-invalid output, or unexpected model
   - a missing secret
   - a PR head that moved during the review
   - a draft PR (the review runs when the PR is marked ready)
5. **Output**: the check `security-review` plus one PR comment from `github-actions[bot]` that is created once and then updated. Model output in the comment is escaped so it cannot mention users, add links or images, or inject HTML.

### Trust boundary

- The workflow uses `pull_request_target`. GitHub always takes the workflow file from the default branch, and the job checks out only `.github/security-review` at that commit. The reviewer code, prompts and thresholds therefore always come from master, never from the PR. A PR that edits the gate is reviewed by the current master version of the gate.
- PR content (metadata, commit messages, patches, post-change file contents, and pre-change contents of sensitive files) is fetched from the GitHub REST API as data. PR code is never checked out, installed, imported or executed in any job that has secrets.
- Permissions: the workflow default is `{}`. The review job has `contents: read` and `pull-requests: read`. The comment job has `pull-requests: write` and never sees the Claude token. Concurrency is one group per PR with cancel-in-progress, so a new push cancels the stale review.
- No caches are used in `pull_request_target` jobs, to avoid cache poisoning.
- Every workflow declares its token permissions explicitly and does not rely on repository defaults.

### No agentic tools

Each pass runs the Claude Code CLI in print mode (`claude -p`) with:

- `--tools ""`, `--strict-mcp-config`, `--setting-sources ""`, `--disable-slash-commands`, `--permission-mode dontAsk` and `--max-turns 1`
- an empty temporary HOME and working directory, and an environment containing only PATH, HOME, locale, telemetry opt-outs and the OAuth token (no `GITHUB_TOKEN`)

The only tool the model has is the CLI's built-in `StructuredOutput` sink for `--json-schema`. `llm.py` parses the CLI's init event and fails closed if any other tool or MCP server is present, or if the model calls any other tool. This was checked with real runs: the model reported no tools, and an instruction to run a shell command had no effect. Prompt injection can at most change the model's words, not make it act.

The CLI is the native Linux x64 binary from the npm registry (`@anthropic-ai/claude-code-linux-x64`), pinned by version and sha256 in `install_claude.sh`. No npm install scripts run. Dependabot does not update this pin; bump it by hand (the steps are in the script header).

### Authentication

The gate uses the repository secret `CLAUDE_CODE_OAUTH_TOKEN`, a Claude subscription OAuth token, passed only to the CLI process. If it is missing or invalid, the check fails. Runs triggered by Dependabot can receive Dependabot secrets instead of Actions secrets. If Dependabot PRs fail with "secret is missing", add the same value as a Dependabot secret.

### Supply-chain hygiene

- Every third-party action in every workflow is pinned to a full commit SHA with a `# vX.Y.Z` comment. Each SHA was checked against the upstream tag with `gh api`.
- `dependency-review` fails PRs that add dependencies with high-severity advisories.
- Dependabot opens weekly PRs for GitHub Actions and pip. They go through the same gate.
- Rulesets (`scripts/setup_ruleset.sh --deploy-key-id ID`, applied by hand):
  - `master`: requires a PR, squash merges and a linear history; blocks force pushes and deletion; applies to admins. Required checks, each bound to the GitHub Actions app so no other app can satisfy them: `security-review`, `dependency-review`, lint, typecheck, gate-tests, the test matrix, the PR title lint job, and tests-ha/hassfest/hacs when present. Check names are read from master's workflows when the script runs.
  - `refs/tags/v*`: creation, update and deletion are blocked.
  - `owner-review` (a second ruleset on master): a PR needs one approval, code owner approval for paths in `.github/CODEOWNERS`, approval of the latest push, and stale approvals are dismissed on push. Its bypass actors are the repository admin role in pull-request mode, so the owner's own PRs merge without a self-approval (which GitHub does not allow anyway), and the release deploy key, whose `release:` commits touch owned files. `master-protection` has no admin bypass, so the owner's PRs still need every required check, `security-review` included.
  - The only bypass actor on both is `DeployKey`. The release bot pushes its `release:` commit and `v*` tag with a write deploy key (secret `RELEASE_DEPLOY_KEY` in the `release` environment, restricted to master). GitHub Actions is deliberately not a bypass actor: that would let any workflow with `contents: write` push to master unreviewed. The rulesets API takes no id for a DeployKey bypass, so it covers every write deploy key. The script refuses to apply if any write deploy key other than the given id exists.

## Workflow and supply-chain changes

A PR that edits workflows, the gate, packaging or release files is the most direct way to compromise this repository. Four layers apply:

1. The gate reviews such PRs in strict mode, with the pre-change content and a medium blocking threshold. Its own code always comes from master.
2. Deterministic pre-checks fail new `pull_request_target`/`workflow_run` triggers, downloads piped to a shell, and anything that could impersonate the `security-review` check.
3. `.github/CODEOWNERS` assigns the paths below to @odtgit, and the `owner-review` ruleset makes his approval mandatory for anyone else's PR. The owned paths are `/.github/` (workflows, the gate, Dependabot config, CODEOWNERS itself), `/Dockerfile`, `/config.yaml` and `/repository.yaml` (add-on image and privileges), `/pyproject.toml` (dependencies, build backend, entry points), `/custom_components/*/manifest.json` and `/hacs.json` (HACS requirements), `/scripts/` (version and release tooling run in CI), `/run.sh` (container entrypoint) and `/evmqtt.service` (systemd unit installed as a service).
4. Fork `pull_request` workflows need the owner's approval to run ("Require approval for all outside collaborators"), so a fork cannot run changed workflow code in CI before he has read it.

Rulesets that target the same branch aggregate, and the most restrictive rule applies (GitHub docs, "About rulesets"). Bypass lists are configured per ruleset. The docs do not state in so many words that bypassing one ruleset leaves the others in force. The design relies on that, so check it once after applying: open a PR as the owner and confirm that the merge box offers the owner-review bypass but still waits for `security-review`.

Push rulesets, which can block pushes that touch given file paths, would be a stricter option. GitHub offers them only for private and internal repositories, so they are not available for this public repository on a personal account.

## Evaluation

The gate was evaluated with the real model on real changes from this repository's history, not on synthetic malware. Fixtures are in `.github/security-review/tests/fixtures/` (made with `fixture_from_git.py`, run with `evaluate.py`):

| Fixture | Source | Expected | Result |
|---|---|---|---|
| hist_devinput_rw_mknod | 5cc7239: /dev/input bind mount made read-write, cgroup rule widened to `c 13:* rmw` | FAIL | FAIL, medium privilege findings in strict mode |
| hist_event_keys_recorded | 0a9e354: key attributes on the HA event entity written to recorder history (fixed in bb44613) | FAIL | FAIL, high keystroke_logging |
| hist_autodiscover_all_enabled | 0856a78: auto-discovered keyboards read and enabled when enabled_devices is empty | FAIL | FAIL, high keystroke_logging |
| benign_docs_readme | 199d6f1: README example | PASS | PASS |
| benign_ble_fix | 9b355cf: Bluetooth LE selection fix | PASS | PASS (one info note) |

The first run passed only low on the recorder fixture. The prompts were then tuned to treat persisted keystrokes as high; the fixtures were not changed. Deterministic pre-checks are unit-tested with single hidden characters, fake binaries and minimal workflow snippets. The gate has not been tested against deliberately malicious code (exfiltration, obfuscated payloads, poisoned workflows, typosquats, injection text). Its behaviour on those is expected from the prompts but not measured.

## Residual risks

- **Prompt injection can still sway the verdict.** Both passes read attacker-controlled text. Mitigations: nonce-tagged data blocks, explicit rules that injection attempts are themselves high findings, an independent verifier told the first pass may be compromised, and deterministic checks that do not depend on the model. None of these is a guarantee.
- **Model judgement.** A subtle backdoor can be rated low or missed, and benign PRs can fail. A human should still read anything touching input handling, networking, workflows or packaging.
- **Cost and abuse.** Approval settings for fork workflows do not apply to `pull_request_target` ("Workflows triggered by `pull_request_target` events are run in the context of the base branch ... will always run, regardless of approval settings", GitHub docs). Anyone can open PRs that spend the subscription's usage. Size limits, draft deferral and per-PR cancellation bound this, but they do not prevent it.
- **Check-name spoofing.** A PR can add a `pull_request` workflow with a job named `security-review`. The pre-check fails such PRs, and "Require approval for all outside collaborators" stops fork `pull_request` workflows from running until a maintainer approves them. Do not approve workflow runs on a fork PR that changes `.github/` before reading the gate's comment. Merging such a PR also needs the owner's code owner approval.
- **Base retargeting.** Changing a PR's base branch does not re-run the gate. Close and reopen the PR (or push) after retargeting.
- **Merge skew.** The review sees the PR against its merge base. The ruleset does not require branches to be up to date, so an interaction with newer master commits is not reviewed.
- **Bypass actor.** Anyone holding a write deploy key can push to master and create `v*` tags without review. Keep exactly one, stored only in the `release` environment, and never add another write deploy key without re-running the script check.
- **Unreviewed inputs.** Images are not shown to the model. File modes, symlinks and mode-only changes are not visible through the API data the gate uses. `pip install` of lint and test tools in CI is unpinned.
- **Spoofing by expression.** The spoofing pre-check matches text. A workflow that builds the job name from an expression can evade it; the AI review and fork run approval are the backstop.
