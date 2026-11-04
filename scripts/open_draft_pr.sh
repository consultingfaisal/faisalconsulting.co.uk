#!/usr/bin/env bash
# Commit a prepared draft on a new branch and open a pull request.
# This script never pushes to main.
set -euo pipefail

: "${TOPIC:?}"
: "${SLUG:?}"
: "${FILENAME:?}"
: "${DATE:?}"
: "${WRITER:?}"
: "${BODYFILE:?}"
QUERY="${QUERY:-}"

if [[ ! "$SLUG" =~ ^[a-z0-9-]+$ ]]; then
  echo "Refusing unsafe slug: ${SLUG}" >&2
  exit 1
fi
if [[ ! "$FILENAME" =~ ^blog-[a-z0-9-]+\.html$ || "$FILENAME" == *"/"* ]]; then
  echo "Refusing unsafe filename: ${FILENAME}" >&2
  exit 1
fi
if [[ ! "$DATE" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
  echo "Refusing unsafe date: ${DATE}" >&2
  exit 1
fi
if [[ ! -s "$BODYFILE" ]]; then
  echo "Missing pull request body: ${BODYFILE}" >&2
  exit 1
fi

# Stop before any commit or push if this calendar row already has an open draft.
# A closed pull request does not count: the row is still queued and can be drafted again.
open_for_row="$(gh pr list --state open --limit 100 --json number,title,body,headRefName | jq -r \
  --arg topic "$TOPIC" \
  --arg date "$DATE" \
  --arg prefix "content-draft/${DATE}-${SLUG}" \
  '.[] | select(
      (.headRefName | startswith($prefix))
      or (.title == ("Draft: " + $topic))
      or (((.body // "") | contains("Calendar date: " + $date)) and ((.body // "") | contains("Topic: " + $topic)))
    ) | "#\(.number) \(.title)"')"
if [[ -n "$open_for_row" ]]; then
  echo "A draft pull request is already open for this calendar row. No second pull request was created:"
  echo "$open_for_row"
  exit 0
fi

branch="content-draft/${DATE}-${SLUG}"
if git ls-remote --heads origin "$branch" | grep -q .; then
  branch="${branch}-${GITHUB_RUN_ID:-manual}"
fi
case "$branch" in
  content-draft/*) ;;
  *)
    echo "Refusing branch name: ${branch}" >&2
    exit 1
    ;;
esac

current="$(git branch --show-current || true)"
if [[ -n "$current" && "$current" != "main" && "$current" != "master" ]]; then
  echo "Refusing to start from ${current}. The draft workflow checks out main first." >&2
  exit 1
fi

git checkout -b "$branch"
if [[ "$(git branch --show-current)" != "$branch" ]]; then
  echo "Failed to switch to ${branch}." >&2
  exit 1
fi

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

git add -- "$FILENAME" blog.html sitemap.xml content/calendar.csv

while read -r file; do
  [[ -z "$file" ]] && continue
  case "$file" in
    "$FILENAME"|blog.html|sitemap.xml|content/calendar.csv) ;;
    *)
      echo "Refusing to commit unexpected file: ${file}" >&2
      exit 1
      ;;
  esac
done < <(git diff --cached --name-only)

if [[ "$(git branch --show-current)" == "main" || "$(git branch --show-current)" == "master" ]]; then
  echo "Refusing to commit on main." >&2
  exit 1
fi

printf -v message 'Draft post: %s\n\nCalendar date: %s\nTarget query: %s\nWritten with: %s\n' \
  "$TOPIC" "$DATE" "$QUERY" "$WRITER"
git commit -m "$message"

refspec="HEAD:${branch}"
case "$refspec" in
  *":main"|*":master"|"main"|"master")
    echo "Refusing to push ${refspec}." >&2
    exit 1
    ;;
esac
git push origin "$refspec"

title="Draft: ${TOPIC}"
if ((${#title} > 240)); then
  title="${title:0:237}..."
fi

pr_url="$(gh pr create --base main --head "$branch" --title "$title" --body-file "$BODYFILE")"
echo "Opened ${pr_url}"
if [[ ! "$pr_url" =~ ^https://github.com/.+/pull/[0-9]+$ ]]; then
  echo "Unexpected pull request URL: ${pr_url}" >&2
  exit 1
fi

python3 scripts/draft_post.py --set-pr-url "$pr_url" --date "$DATE" --topic "$TOPIC"
git add -- content/calendar.csv
git commit -m "Note the draft pull request on the content calendar."
git push origin "$refspec"
