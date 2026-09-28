#!/usr/bin/env bash
# Pinned Claude Code native binary from the npm registry, verified by sha256. No install scripts run.
# Bump: set VERSION, then SHA256 = sha256sum of the tarball below (npm view @anthropic-ai/claude-code-linux-x64@VERSION dist.integrity cross-checks it).
set -euo pipefail

VERSION="2.1.283"
SHA256="d14ec0fca400151092c926928fea7e8e38231f7a90e6e1f8eeb2a55c24676afb"
URL="https://registry.npmjs.org/@anthropic-ai/claude-code-linux-x64/-/claude-code-linux-x64-${VERSION}.tgz"

[ "$(uname -s)-$(uname -m)" = "Linux-x86_64" ] || { echo "::error::unsupported runner $(uname -sm)"; exit 1; }
dest="${1:?usage: install_claude.sh DEST_DIR}"
mkdir -p "$dest"
tgz="$dest/claude.tgz"
curl -fsSL --proto '=https' --tlsv1.2 -o "$tgz" "$URL"
echo "${SHA256}  ${tgz}" | sha256sum -c --quiet -
tar -xzf "$tgz" -C "$dest" --strip-components=1 package/claude
rm -f "$tgz"
chmod 0755 "$dest/claude"
"$dest/claude" --version
