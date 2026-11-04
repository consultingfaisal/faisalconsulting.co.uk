#!/usr/bin/env python3
"""Set a newly merged post's dates to the day it reached main.

The content calendar date is a plan. The page, the Insights card and the
sitemap show the day the post was published. This never writes a future date.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
VISIBLE_DATE = re.compile(r"\b\d{1,2} (" + "|".join(MONTHS) + r") \d{4}\b")


def uk_date(value: date) -> str:
    return f"{value.day} {value.strftime('%B %Y')}"


def today_utc() -> date:
    return datetime.now(timezone.utc).date()


def is_blog_post(html: str) -> bool:
    return '"@type":"BlogPosting"' in html or '"@type": "BlogPosting"' in html


def stamp_post_html(html: str, iso: str, visible: str) -> str:
    html = re.sub(r'("datePublished"\s*:\s*")\d{4}-\d{2}-\d{2}(")', rf"\g<1>{iso}\2", html)
    html = re.sub(r'("dateModified"\s*:\s*")\d{4}-\d{2}-\d{2}(")', rf"\g<1>{iso}\2", html)

    def hero(match: re.Match[str]) -> str:
        inner = VISIBLE_DATE.sub(visible, match.group(1), count=1)
        return f'<p class="hero-sub">{inner}</p>'

    return re.sub(r'<p class="hero-sub">(.*?)</p>', hero, html, count=1, flags=re.S)


def stamp_card(blog_html: str, filename: str, visible: str) -> str:
    pattern = rf'(<a href="{re.escape(filename)}">\s*<p class="blog-card-date">)(.*?)(</p>)'

    def repl(match: re.Match[str]) -> str:
        inner = VISIBLE_DATE.sub(visible, match.group(2), count=1)
        return match.group(1) + inner + match.group(3)

    return re.sub(pattern, repl, blog_html, count=1, flags=re.S)


def stamp_sitemap(xml: str, filename: str, iso: str) -> str:
    loc = f"https://faisalconsulting.co.uk/{filename}"
    pattern = rf"(<loc>\s*{re.escape(loc)}\s*</loc>\s*<lastmod>)\d{{4}}-\d{{2}}-\d{{2}}(</lastmod>)"
    return re.sub(pattern, rf"\g<1>{iso}\2", xml, count=1)


def added_files(root: Path, before: str, after: str) -> list[str]:
    if not before or set(before) <= {"0"}:
        command = ["git", "diff-tree", "--no-commit-id", "--name-only", "--diff-filter=A", "-r", after or "HEAD"]
    else:
        command = ["git", "diff", "--name-only", "--diff-filter=A", before, after]
    result = subprocess.run(
        command,
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def stamp_new_posts(root: Path, files: list[str], published: date) -> list[str]:
    iso = published.isoformat()
    visible = uk_date(published)
    changed: list[str] = []
    blog_path = root / "blog.html"
    sitemap_path = root / "sitemap.xml"
    blog_html = blog_path.read_text(encoding="utf-8") if blog_path.is_file() else ""
    sitemap = sitemap_path.read_text(encoding="utf-8") if sitemap_path.is_file() else ""
    blog_changed = False
    sitemap_changed = False
    for name in files:
        if not name.endswith(".html"):
            continue
        path = root / name
        if not path.is_file():
            continue
        original = path.read_text(encoding="utf-8")
        if not is_blog_post(original):
            continue
        updated = stamp_post_html(original, iso, visible)
        if updated != original:
            path.write_text(updated, encoding="utf-8")
            changed.append(name)
        filename = Path(name).name
        if blog_html:
            stamped = stamp_card(blog_html, filename, visible)
            if stamped != blog_html:
                blog_html = stamped
                blog_changed = True
        if sitemap:
            stamped_map = stamp_sitemap(sitemap, filename, iso)
            if stamped_map != sitemap:
                sitemap = stamped_map
                sitemap_changed = True
    if blog_changed:
        blog_path.write_text(blog_html, encoding="utf-8")
        changed.append("blog.html")
    if sitemap_changed:
        sitemap_path.write_text(sitemap, encoding="utf-8")
        changed.append("sitemap.xml")
    return changed


def set_output(name: str, value: str) -> None:
    print(f"{name}={value}")
    path = __import__("os").environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")


def self_test() -> None:
    sample = """<p class="hero-sub">10 November 2026</p>
<script type="application/ld+json">{"datePublished":"2026-11-10","dateModified":"2026-11-10"}</script>
"""
    stamped = stamp_post_html(sample, "2026-11-04", "4 November 2026")
    assert "2026-11-04" in stamped
    assert "2026-11-10" not in stamped
    assert "4 November 2026" in stamped
    card = '<a href="blog-example.html">\n        <p class="blog-card-date">10 November 2026</p>'
    assert "4 November 2026" in stamp_card(card, "blog-example.html", "4 November 2026")
    xml = "<loc>https://faisalconsulting.co.uk/blog-example.html</loc><lastmod>2026-11-10</lastmod>"
    assert "2026-11-04" in stamp_sitemap(xml, "blog-example.html", "2026-11-04")
    print("Publish-date self-test passed.", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description="Stamp newly added posts with the day they reached main.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--before", default="")
    parser.add_argument("--after", default="HEAD")
    parser.add_argument("--date", default="", help="YYYY-MM-DD. Defaults to today, UTC.")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    root = Path(args.root).resolve()
    published = date.fromisoformat(args.date) if args.date else today_utc()
    if published > today_utc():
        print(f"Refusing a future publish date: {published.isoformat()}", file=sys.stderr)
        return 1
    files = added_files(root, args.before, args.after)
    changed = stamp_new_posts(root, files, published)
    set_output("changed", "true" if changed else "false")
    if changed:
        print("Updated publish dates in: " + ", ".join(changed), file=sys.stderr)
    else:
        print("No new posts needed a publish date.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
