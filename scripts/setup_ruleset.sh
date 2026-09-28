#!/usr/bin/env bash
# Create or update the master branch ruleset. Run by hand after review; nothing runs this automatically.
# Usage: scripts/setup_ruleset.sh [--dry-run] [--repo owner/name]
set -euo pipefail

REPO="odtgit/evmqtt"
BRANCH="master"
NAME="master-protection"
ACTIONS_APP_ID=15368
DRY_RUN=0

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --repo) REPO="$2"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

app_id=$(gh api /apps/github-actions --jq .id)
[ "$app_id" = "$ACTIONS_APP_ID" ] || { echo "GitHub Actions app id is $app_id, expected $ACTIONS_APP_ID" >&2; exit 1; }

ci=$(gh api -H "Accept: application/vnd.github.raw+json" "repos/$REPO/contents/.github/workflows/ci.yml?ref=$BRANCH")

payload=$(CI_YML="$ci" NAME="$NAME" BRANCH="$BRANCH" APP="$ACTIONS_APP_ID" python3 - <<'PY'
import json, os, re, sys

ci = os.environ["CI_YML"]
jobs = set(re.findall(r"^  ([A-Za-z0-9_-]+):\s*$", ci, re.M))
checks = ["security-review", "dependency-review"]
for job in ("lint", "typecheck", "gate-tests", "tests-ha", "hassfest", "hacs"):
    if job in jobs:
        checks.append(job)
if "test" not in jobs:
    sys.exit("ci.yml has no test job")
block = ci.split("\n  test:\n", 1)[1]
m = re.search(r"python-version:\s*\[([^\]]+)\]", block)
if not m:
    sys.exit("cannot read the test matrix from ci.yml")
checks += [f"test ({v.strip().strip(chr(34)).strip(chr(39))})" for v in m.group(1).split(",")]
app = int(os.environ["APP"])
print(json.dumps({
    "name": os.environ["NAME"],
    "target": "branch",
    "enforcement": "active",
    "conditions": {"ref_name": {"include": [f"refs/heads/{os.environ['BRANCH']}"], "exclude": []}},
    "bypass_actors": [{"actor_id": app, "actor_type": "Integration", "bypass_mode": "always"}],
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
}, indent=2))
PY
)

existing=$(gh api "repos/$REPO/rulesets" --jq ".[] | select(.name == \"$NAME\") | .id")

if [ "$DRY_RUN" = 1 ]; then
  if [ -n "$existing" ]; then echo "# would PUT repos/$REPO/rulesets/$existing"; else echo "# would POST repos/$REPO/rulesets"; fi
  echo "$payload"
  exit 0
fi

if [ -n "$existing" ]; then
  echo "$payload" | gh api -X PUT "repos/$REPO/rulesets/$existing" --input - --jq '"updated ruleset \(.id)"'
else
  echo "$payload" | gh api -X POST "repos/$REPO/rulesets" --input - --jq '"created ruleset \(.id)"'
fi
