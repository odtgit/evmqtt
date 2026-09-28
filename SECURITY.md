# Security

## Reporting a vulnerability

Use GitHub's private vulnerability reporting (Security tab, "Report a vulnerability"). Do not open a public issue.

## Why this repository is gated

evmqtt reads raw input events on Home Assistant hosts: every key typed on a watched keyboard, passwords included. The add-on runs with host networking, full access and SYS_RAWIO. Merges to master are released automatically to PyPI, GHCR and Home Assistant users (add-on and HACS). A malicious or careless change can therefore reach many hosts with no further human step. The only control before merge is the `security-review` check described here, backed by the ruleset in `scripts/setup_ruleset.sh`.

## The gate

Workflow: `.github/workflows/security-review.yml`. Code: `.github/security-review/`.

1. **Deterministic pre-checks** (`policy.py`, no AI, run first, hard fail):
   - bidi and zero-width characters (U+202A..U+202E, U+2066..U+2069, U+200B..U+200F, U+FEFF, U+2028, U+2029, U+061C) in added lines, file paths, PR title or body
   - added or changed binary files, except PNG/JPEG/GIF/ICO images whose magic bytes match and that are under 1 MB
   - size limits: more than 150 files, a diff over 200k characters, review context over 800k characters, a single file over 250k characters, or a file GitHub will not diff. The author is told to split the PR.
   - `pull_request_target` added to any workflow other than the gate, `curl|sh` style downloads in sensitive files, and any other workflow that defines a job or check named `security-review` (status spoofing)
   - files in the gate's fixture directory that are not inert `*.fx` or `meta.json`
2. **Sensitive paths** switch the AI review to strict mode instead of failing: `.github/**`, Dockerfiles, compose files, add-on `config.yaml`/`build.yaml`/`repository.yaml`, `pyproject.toml`, `setup.*`, requirements files, `custom_components/*/manifest.json`, `hacs.json`, `run.sh`, `scripts/**`, `*.service`, `.gitattributes`, `.gitmodules`, pre-commit config and this file. In strict mode the pre-change content of those files is included and the blocking threshold drops from high to medium.
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
- The ruleset (`scripts/setup_ruleset.sh`, applied by hand) makes the following checks required on master, each bound to the GitHub Actions app so no other app can satisfy them: `security-review`, `dependency-review`, lint, typecheck, gate-tests, the test matrix, and tests-ha/hassfest/hacs when present. It also requires a PR, a linear history and squash merges, blocks force pushes and deletion, and applies to admins. The only bypass actor is GitHub Actions (app 15368), so the auto-release bot can push its `release:` commit.

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
- **Check-name spoofing.** A PR can add a `pull_request` workflow with a job named `security-review`. The pre-check fails such PRs, and "Require approval for all outside collaborators" stops fork `pull_request` workflows from running until a maintainer approves them. Do not approve workflow runs on a fork PR that changes `.github/` before reading the gate's comment.
- **Base retargeting.** Changing a PR's base branch does not re-run the gate. Close and reopen the PR (or push) after retargeting.
- **Merge skew.** The review sees the PR against its merge base. The ruleset does not require branches to be up to date, so an interaction with newer master commits is not reviewed.
- **Bypass actor.** Any workflow on master that has `contents: write` can push past the ruleset through the GitHub Actions bypass. Keep write permissions limited to the release job.
- **Unreviewed inputs.** Images are not shown to the model. `pip install` of lint and test tools in CI is unpinned.
