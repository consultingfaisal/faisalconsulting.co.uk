#!/usr/bin/env python3
"""Build the minified CSS and JavaScript the site serves, and point every page at them.

Pages load style.min.css and site.min.js. style.css and site.js stay in the
repository as the readable sources. Edit those, then run:

    python3 scripts/minify_assets.py

That rebuilds the .min files with esbuild and updates the ?v= cache buster on
every page to a hash of the new file, so browsers fetch the new version.

    python3 scripts/minify_assets.py --check

changes nothing. It fails if a .min file is out of date or any page still loads
an unminified file. The "Check minified assets" workflow runs it on every pull
request and every push to main.

esbuild is pinned so the output is the same on every machine. Set ESBUILD to
the path of an esbuild binary to skip npx.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ESBUILD_VERSION = "0.24.2"

# source file -> (minified file, pattern matching any reference to either one)
ASSETS = {
    "style.css": ("style.min.css", re.compile(r'href="style(?:\.min)?\.css(?:\?v=[^"]*)?"')),
    "site.js": ("site.min.js", re.compile(r'src="site(?:\.min)?\.js(?:\?v=[^"]*)?"')),
}


def esbuild_cmd() -> list[str]:
    explicit = os.environ.get("ESBUILD")
    if explicit:
        return [explicit]
    if not shutil.which("npx"):
        sys.exit("npx was not found. Install Node.js, or set ESBUILD to an esbuild binary.")
    return ["npx", "--yes", f"esbuild@{ESBUILD_VERSION}"]


def minify(source: Path, out: Path) -> None:
    cmd = esbuild_cmd() + [
        str(source),
        "--minify",
        "--legal-comments=none",
        "--log-level=warning",
        f"--outfile={out}",
    ]
    subprocess.run(cmd, check=True, cwd=ROOT)


def version_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:10]


def pages() -> list[Path]:
    return sorted(ROOT.glob("*.html"))


def attribute(source: str, minified: str, version: str) -> str:
    name = "href" if source.endswith(".css") else "src"
    return f'{name}="{minified}?v={version}"'


def build() -> int:
    changed_pages = 0
    for source, (minified, pattern) in ASSETS.items():
        minify(ROOT / source, ROOT / minified)
        wanted = attribute(source, minified, version_of(ROOT / minified))
        for page in pages():
            text = page.read_text(encoding="utf-8")
            new = pattern.sub(wanted, text)
            if new != text:
                page.write_text(new, encoding="utf-8")
                changed_pages += 1
        before = (ROOT / source).stat().st_size
        after = (ROOT / minified).stat().st_size
        print(f"{minified}: {before:,} -> {after:,} bytes ({wanted})")
    print(f"Updated {changed_pages} page references.")
    return 0


def check() -> int:
    problems: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        for source, (minified, pattern) in ASSETS.items():
            fresh = Path(tmp) / minified
            minify(ROOT / source, fresh)
            committed = ROOT / minified
            if not committed.exists() or committed.read_bytes() != fresh.read_bytes():
                problems.append(f"{minified} is out of date with {source}.")
                continue
            wanted = attribute(source, minified, version_of(committed))
            for page in pages():
                for found in pattern.findall(page.read_text(encoding="utf-8")):
                    if found != wanted:
                        problems.append(f"{page.name} loads {found}, expected {wanted}.")
    if problems:
        print("Minified assets need rebuilding. Run: python3 scripts/minify_assets.py")
        for line in problems[:50]:
            print(f"  {line}")
        if len(problems) > 50:
            print(f"  ...and {len(problems) - 50} more.")
        return 1
    print("Minified assets are up to date and every page loads them.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="Change nothing; fail if anything is stale.")
    args = parser.parse_args()
    return check() if args.check else build()


if __name__ == "__main__":
    sys.exit(main())
