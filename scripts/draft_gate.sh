#!/usr/bin/env bash
# Skip a new draft when one content-draft pull request is already open.
set -euo pipefail

output="${GITHUB_OUTPUT:-/dev/null}"
prs="$(gh pr list --state open --limit 100 --json number,title,headRefName)"
open="$(printf '%s' "$prs" | jq -r '.[] | select(.headRefName | startswith("content-draft/")) | "#\(.number) \(.title)"')"

if [[ -n "$open" ]]; then
  echo "waiting=true" >> "$output"
  echo "A draft pull request is already open, so no new draft was created:"
  echo "$open"
  echo "::notice title=Draft already open::$(echo "$open" | head -n 1)"
else
  echo "waiting=false" >> "$output"
fi
