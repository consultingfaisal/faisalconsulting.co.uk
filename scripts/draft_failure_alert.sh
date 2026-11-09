#!/usr/bin/env bash
# Tell Faisal the same day when the "Draft blog post" run fails.
# fail: open one issue that mentions @consultingfaisal (GitHub emails him),
#       or comment on the open one if an earlier failure is not yet fixed.
# ok:   close the open alert once a draft pull request has been opened.
set -euo pipefail

mode="${1:?fail or ok}"
run_url="${RUN_URL:?run url}"
label="draft-failed"

gh label create "$label" \
  --color B60205 \
  --description "The Draft blog post run failed" >/dev/null 2>&1 || true

open="$(gh issue list --label "$label" --state open --limit 1 --json number --jq '.[0].number // empty')"

if [[ "$mode" == "fail" ]]; then
  body="@consultingfaisal the Draft blog post run failed on $(date -u '+%d %b %Y at %H:%M UTC').

Run: ${run_url}

No pull request was opened. The calendar row is still queued, so the next scheduled run (Tuesday or Thursday at 07:00 UTC) will try again.

To try now: Actions, Draft blog post, Run workflow.

If the log shows HTTP 401 or 403, the Gemini key needs replacing in Settings, Secrets, GEMINI_API_KEY. HTTP 503 or 429 means Gemini was busy; a rerun later usually works.

<!-- draft-failure-bot -->"
  if [[ -n "$open" ]]; then
    gh issue comment "$open" --body "$body"
    echo "Commented on issue #${open}."
  else
    gh issue create --title "Draft blog post failed" --label "$label" --body "$body"
  fi
elif [[ "$mode" == "ok" ]]; then
  if [[ -n "$open" ]]; then
    gh issue close "$open" --comment "Fixed. A draft pull request was opened by ${run_url}"
  fi
else
  echo "Unknown mode: ${mode}" >&2
  exit 1
fi
