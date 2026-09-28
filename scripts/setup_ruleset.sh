#!/usr/bin/env bash
# Create or update the master branch and v* tag rulesets. Run by hand after review; nothing runs this automatically.
# Usage: scripts/setup_ruleset.sh --deploy-key-id ID [--dry-run] [--repo owner/name] [--workflows-from DIR]
# --workflows-from reads ci.yml and pr-title-lint.yml from a local directory instead of master (previews).
#
# The only bypass actor is DeployKey. The rulesets API takes no id for it (actor_id must be null),
# so the bypass covers every deploy key with write access. The script therefore requires that
# --deploy-key-id is the repository's only write deploy key.
set -euo pipefail

REPO="odtgit/evmqtt"
BRANCH="master"
BRANCH_RULESET="master-protection"
TAG_RULESET="release-tags"
ACTIONS_APP_ID=15368
KEY_ID=""
DRY_RUN=0
LOCAL=""

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --repo) REPO="$2"; shift ;;
    --deploy-key-id) KEY_ID="$2"; shift ;;
    --workflows-from) LOCAL="$2"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done
[ -n "$KEY_ID" ] || { echo "--deploy-key-id is required (gh api repos/$REPO/keys)" >&2; exit 2; }

warn_or_die() {
  if [ "$DRY_RUN" = 1 ]; then echo "# WARNING: $*" >&2; else echo "$*" >&2; exit 1; fi
}

app_id=$(gh api /apps/github-actions --jq .id)
[ "$app_id" = "$ACTIONS_APP_ID" ] || { echo "GitHub Actions app id is $app_id, expected $ACTIONS_APP_ID" >&2; exit 1; }

keys=$(gh api "repos/$REPO/keys" --paginate --jq '.[] | "\(.id) \(.read_only) \(.title)"')
echo "$keys" | awk -v id="$KEY_ID" '$1 == id && $2 == "false" { found = 1 } END { exit !found }' \
  || warn_or_die "deploy key $KEY_ID not found or read-only in $REPO"
others=$(echo "$keys" | awk -v id="$KEY_ID" 'NF && $1 != id && $2 == "false"')
[ -z "$others" ] || warn_or_die "other write deploy keys would also bypass the rulesets: $others"

fetch() {
  local out
  if [ -n "$LOCAL" ]; then
    if [ -f "$LOCAL/$(basename "$1")" ]; then cat "$LOCAL/$(basename "$1")"; fi
    return 0
  fi
  if out=$(gh api -H "Accept: application/vnd.github.raw+json" "repos/$REPO/contents/$1?ref=$BRANCH" 2>/dev/null); then
    printf '%s\n' "$out"
  fi
}
ci=$(fetch .github/workflows/ci.yml)
[ -n "$ci" ] || { echo "cannot read ci.yml from $BRANCH" >&2; exit 1; }
title=$(fetch .github/workflows/pr-title-lint.yml)
[ -n "$title" ] || echo "# WARNING: no pr-title-lint.yml on $BRANCH, title lint not required" >&2

payloads=$(CI_YML="$ci" TITLE_YML="$title" BRANCH="$BRANCH" APP="$ACTIONS_APP_ID" \
  BRANCH_RULESET="$BRANCH_RULESET" TAG_RULESET="$TAG_RULESET" python3 - <<'PY'
import json, os, re, sys


def jobs(text):
    """job id -> check name (job name: if set, else the id); matrix jobs return the id."""
    out = {}
    body = text.split("\njobs:\n", 1)[1] if "\njobs:\n" in text else ""
    for block in re.split(r"\n(?=  [A-Za-z0-9_-]+:\s*\n)", "\n" + body):
        m = re.match(r"\s*([A-Za-z0-9_-]+):\s*\n", block)
        if not m:
            continue
        name = re.search(r"^    name:\s*['\"]?([^'\"\n]+?)['\"]?\s*$", block, re.M)
        out[m.group(1)] = (name.group(1) if name else m.group(1), block)
    return out


ci = jobs(os.environ["CI_YML"])
checks = ["security-review", "dependency-review"]
for job in ("lint", "typecheck", "gate-tests", "tests-ha", "hassfest", "hacs"):
    if job in ci:
        checks.append(ci[job][0])
if "test" not in ci:
    sys.exit("ci.yml has no test job")
m = re.search(r"python-version:\s*\[([^\]]+)\]", ci["test"][1])
if not m:
    sys.exit("cannot read the test matrix from ci.yml")
checks += [f"test ({v.strip().strip(chr(34)).strip(chr(39))})" for v in m.group(1).split(",")]
if os.environ["TITLE_YML"]:
    checks += [name for name, _ in jobs(os.environ["TITLE_YML"]).values()]

app = int(os.environ["APP"])
bypass = [{"actor_id": None, "actor_type": "DeployKey", "bypass_mode": "always"}]
branch = {
    "name": os.environ["BRANCH_RULESET"],
    "target": "branch",
    "enforcement": "active",
    "conditions": {"ref_name": {"include": [f"refs/heads/{os.environ['BRANCH']}"], "exclude": []}},
    "bypass_actors": bypass,
    "rules": [
        {"type": "deletion"},
        {"type": "non_fast_forward"},
        {"type": "required_linear_history"},
        {"type": "pull_request", "parameters": {
            "required_approving_review_count": 0,
            "dismiss_stale_reviews_on_push": False,
            "require_code_owner_review": False,
            "require_last_push_approval": False,
            "required_review_thread_resolution": False,
            "allowed_merge_methods": ["squash"],
        }},
        {"type": "required_status_checks", "parameters": {
            "strict_required_status_checks_policy": False,
            "do_not_enforce_on_create": False,
            "required_status_checks": [{"context": c, "integration_id": app} for c in checks],
        }},
    ],
}
tag = {
    "name": os.environ["TAG_RULESET"],
    "target": "tag",
    "enforcement": "active",
    "conditions": {"ref_name": {"include": ["refs/tags/v*"], "exclude": []}},
    "bypass_actors": bypass,
    "rules": [{"type": "creation"}, {"type": "update"}, {"type": "deletion"}],
}
print(json.dumps(branch))
print(json.dumps(tag))
PY
)

existing=$(gh api "repos/$REPO/rulesets" --paginate --jq '.[] | "\(.name)\t\(.id)"')

apply() {
  local name="$1" payload="$2" id
  id=$(echo "$existing" | awk -F'\t' -v n="$name" '$1 == n { print $2 }')
  if [ "$DRY_RUN" = 1 ]; then
    if [ -n "$id" ]; then echo "# would PUT repos/$REPO/rulesets/$id"; else echo "# would POST repos/$REPO/rulesets"; fi
    echo "$payload" | python3 -m json.tool
  elif [ -n "$id" ]; then
    echo "$payload" | gh api -X PUT "repos/$REPO/rulesets/$id" --input - --jq '"updated ruleset \(.name) \(.id)"'
  else
    echo "$payload" | gh api -X POST "repos/$REPO/rulesets" --input - --jq '"created ruleset \(.name) \(.id)"'
  fi
}

apply "$BRANCH_RULESET" "$(echo "$payloads" | sed -n 1p)"
apply "$TAG_RULESET" "$(echo "$payloads" | sed -n 2p)"
