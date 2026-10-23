#!/usr/bin/env bash
set -euo pipefail
TOKEN="${GITHUB_TOKEN:-${GH_TOKEN:-}}"
if [[ -z "$TOKEN" && -f /tmp/github_token ]]; then
  TOKEN=$(tr -d ' \n\r' < /tmp/github_token)
fi
if [[ -z "$TOKEN" ]]; then
  echo "Set GITHUB_TOKEN or write the PAT to /tmp/github_token" >&2
  exit 1
fi
cd /tmp/al-publish
git push "https://x-access-token:${TOKEN}@github.com/sebvett/adaptive-lighting.git" HEAD:main
echo "Pushed to https://github.com/sebvett/adaptive-lighting"
