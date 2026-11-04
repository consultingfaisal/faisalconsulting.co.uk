#!/usr/bin/env python3
"""Tell IndexNow about posts added or changed on main.

Looks for a key file at the site root: the file name is the key plus .txt,
and the file contains that same key. A failed ping is logged and ignored.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

SITE = "https://faisalconsulting.co.uk"
HOST = "faisalconsulting.co.uk"
ENDPOINT = "https://api.indexnow.org/indexnow"


def is_blog_post(html: str) -> bool:
    return '"@type":"BlogPosting"' in html or '"@type": "BlogPosting"' in html


def changed_posts(root: Path, before: str, after: str) -> list[str]:
    if not before or set(before) <= {"0"}:
        revisions = ["HEAD"]
    else:
        revisions = [before, after]
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=AM", *revisions],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(result.stderr.strip() or "Could not list changed files.", file=sys.stderr)
        return []
    names: list[str] = []
    for line in result.stdout.splitlines():
        name = line.strip()
        if not name.endswith(".html"):
            continue
        path = root / name
        if path.is_file() and is_blog_post(path.read_text(encoding="utf-8", errors="replace")):
            names.append(name)
    return names


def find_key(root: Path) -> tuple[str, str] | None:
    for path in sorted(root.glob("*.txt")):
        key = path.read_text(encoding="utf-8", errors="replace").strip()
        if key and path.name == f"{key}.txt" and 8 <= len(key) <= 128 and key.replace("-", "").isalnum():
            return key, f"{SITE}/{path.name}"
    return None


def ping(urls: list[str], key: str, key_location: str) -> None:
    payload = {
        "host": HOST,
        "key": key,
        "keyLocation": key_location,
        "urlList": urls,
    }
    request = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            print(f"IndexNow accepted {len(urls)} URL(s) with HTTP {response.status}.", file=sys.stderr)
    except Exception as exc:
        detail = str(exc)
        if isinstance(exc, urllib.error.HTTPError):
            detail = f"HTTP {exc.code}"
        print(f"IndexNow ping failed ({detail}). The site change is still published.", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description="Ping IndexNow for changed blog posts.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--before", default="")
    parser.add_argument("--after", default="HEAD")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    if args.self_test:
        found = find_key(root)
        if found is None:
            print("IndexNow self-test failed. No key file at the site root.", file=sys.stderr)
            return 1
        key, location = found
        assert location.endswith(f"/{key}.txt")
        print("IndexNow self-test passed.", file=sys.stderr)
        return 0
    names = changed_posts(root, args.before, args.after)
    if not names:
        print("No changed blog posts to submit.", file=sys.stderr)
        return 0
    found = find_key(root)
    if found is None:
        print("No IndexNow key file at the site root. Skipping the ping.", file=sys.stderr)
        return 0
    key, location = found
    urls = [f"{SITE}/{Path(name).name}" for name in names]
    ping(urls, key, location)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"IndexNow ping failed ({exc}). The site change is still published.", file=sys.stderr)
        raise SystemExit(0)
