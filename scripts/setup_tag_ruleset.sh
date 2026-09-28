#!/usr/bin/env bash
# Create or update a repository ruleset on refs/tags/v* that blocks tag
# creation, update and deletion for everyone except a deploy key with write
# access (the release identity's push/tag credential). Run manually by the
# owner; not invoked by any workflow. See RELEASING.md for the full setup
# (deploy key generation, secret, environment).
#
# Usage: scripts/setup_tag_ruleset.sh <owner/repo>
# Requires: gh CLI authenticated with admin rights on the repo.

set -euo pipefail

REPO="${1:?usage: setup_tag_ruleset.sh <owner/repo>}"
NAME="release-tag-protection"

existing_id=$(gh api "repos/$REPO/rulesets" --jq ".[] | select(.name==\"$NAME\") | .id" | head -n1 || true)

payload=$(
  cat <<'JSON'
{
  "name": "release-tag-protection",
  "target": "tag",
  "enforcement": "active",
  "conditions": {
    "ref_name": {
      "include": ["refs/tags/v*"],
      "exclude": []
    }
  },
  "rules": [
    { "type": "creation" },
    { "type": "update" },
    { "type": "deletion" }
  ],
  "bypass_actors": [
    { "actor_type": "DeployKey", "actor_id": null, "bypass_mode": "always" }
  ]
}
JSON
)

if [ -n "$existing_id" ]; then
  echo "updating existing ruleset $existing_id ($NAME) on $REPO"
  echo "$payload" | gh api "repos/$REPO/rulesets/$existing_id" -X PUT --input -
else
  echo "creating ruleset $NAME on $REPO"
  echo "$payload" | gh api "repos/$REPO/rulesets" -X POST --input -
fi
