#!/usr/bin/env bash
# Open the health report as one GitHub issue, then close older bot reports.
set -euo pipefail

report="${1:?path to the health report}"
if [[ ! -s "$report" ]]; then
  echo "No health report at ${report}." >&2
  exit 1
fi

title="$(head -n 1 "$report" | sed 's/^# //')"
if [[ -z "$title" ]]; then
  title="Site health check"
fi
if ((${#title} > 240)); then
  title="${title:0:237}..."
fi

gh label create site-health \
  --color 0B3B2C \
  --description "Weekly site health report" >/dev/null 2>&1 || true

url="$(gh issue create --title "$title" --label site-health --body-file "$report")"
echo "Opened ${url}"
number="${url##*/}"

while read -r old; do
  [[ -z "$old" || "$old" == "$number" ]] && continue
  gh issue close "$old" --comment "Closing this report. A newer site health check replaces it: ${url}"
done < <(gh issue list --label site-health --state open --limit 50 --json number,body --jq '.[] | select(.body | contains("site-health-bot")) | .number')
