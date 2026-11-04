#!/usr/bin/env python3
"""Weekly site health check for faisalconsulting.co.uk.

Reads the live site and the HTML in this repository. Writes a plain-English
report. Does not modify the site. Uses the Python standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree as ET
import urllib.error
import urllib.request

SITE = "https://faisalconsulting.co.uk"
HOST = "faisalconsulting.co.uk"
TITLE_MIN, TITLE_MAX = 30, 60
DESC_MIN, DESC_MAX = 70, 160
MAX_BODY = 2_000_000
TIMEOUT = 25
WORKERS = 4
USER_AGENT = (
    "FaisalConsultingSiteHealth/1.0 "
    "(+https://faisalconsulting.co.uk/; weekly GitHub Action)"
)
ROBOTS_DIRECTIVES = {
    "user-agent",
    "allow",
    "disallow",
    "sitemap",
    "crawl-delay",
    "host",
}
REDIRECTS = {301, 302, 303, 307, 308}
JSONLD_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _NoError(urllib.request.HTTPErrorProcessor):
    def http_response(self, request, response):
        return response

    https_response = http_response


OPENER = urllib.request.build_opener(_NoRedirect, _NoError)
PRINT_LOCK = threading.Lock()


def log(message: str) -> None:
    with PRINT_LOCK:
        print(message, file=sys.stderr)


def norm(url: str) -> str:
    """Normalise a URL for comparison. Keeps the query string. Drops fragments."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        return url.strip()
    scheme = (parts.scheme or "https").lower()
    port = parts.port
    netloc = host
    if port and not (
        (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    ):
        netloc = f"{host}:{port}"
    path = unquote(parts.path or "/")
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    if path.lower() == "/index.html":
        path = "/"
    out = f"{scheme}://{netloc}{path}"
    if parts.query:
        out += "?" + parts.query
    return out


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().rstrip(".")


def short_path(url: str) -> str:
    path = urlsplit(url).path or "/"
    if path.lower() == "/index.html":
        return "/"
    return path or "/"


def url_to_relpath(url: str) -> str | None:
    parts = urlsplit(url)
    host = host_of(url)
    if host and host != HOST:
        return None
    path = unquote(parts.path or "/")
    if path in ("", "/"):
        rel = "index.html"
    elif path.endswith("/"):
        rel = path.lstrip("/") + "index.html"
    else:
        rel = path.lstrip("/")
    if not rel or any(part in ("", ".", "..") for part in Path(rel).parts):
        return None
    return rel


def collapse(text: str) -> str:
    return " ".join(text.split())


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def validate_robots(text: str) -> list[str]:
    problems: list[str] = []
    if not text.strip():
        return ["The file is empty."]
    if text.lstrip().startswith("<"):
        return ["The file looks like HTML, not a robots.txt file."]
    saw_agent = False
    sitemap_urls: list[str] = []
    for number, line in enumerate(text.splitlines(), 1):
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        if ":" not in raw:
            problems.append(f"Line {number} is not a robots directive.")
            continue
        key, value = raw.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key not in ROBOTS_DIRECTIVES:
            problems.append(f"Line {number} uses an unknown directive ({key}).")
        if key == "user-agent" and value:
            saw_agent = True
        if key == "sitemap":
            sitemap_urls.append(value)
    if not saw_agent:
        problems.append("There is no User-agent line.")
    expected = norm(SITE + "/sitemap.xml")
    if not sitemap_urls:
        problems.append("There is no Sitemap line.")
    elif not any(norm(item) == expected for item in sitemap_urls):
        problems.append(f"The Sitemap line does not point at {SITE}/sitemap.xml.")
    return problems


def validate_llms(text: str) -> list[str]:
    problems: list[str] = []
    stripped = text.lstrip().lower()
    if not text.strip():
        problems.append("The file is empty.")
    if stripped.startswith("<!doctype") or stripped.startswith("<html"):
        problems.append("The file looks like a web page, not llms.txt.")
    if "faisal" not in text.lower():
        problems.append("The file does not mention Faisal.")
    return problems


def parse_sitemap(text: str) -> tuple[list[str], list[str]]:
    problems: list[str] = []
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        return [], [f"The sitemap is not valid XML ({exc})."]
    locs: list[str] = []
    seen: set[str] = set()
    for el in root.iter():
        if local_name(el.tag) != "url":
            continue
        loc = ""
        lastmod = ""
        for child in list(el):
            name = local_name(child.tag)
            value = (child.text or "").strip()
            if name == "loc":
                loc = value
            elif name == "lastmod":
                lastmod = value
        if not loc:
            problems.append("A sitemap entry has no URL.")
            continue
        locs.append(loc)
        parts = urlsplit(loc)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            problems.append(f"URL is not absolute: {loc}")
        elif host_of(loc) != HOST:
            problems.append(f"URL is on a different site: {loc}")
        elif parts.scheme != "https":
            problems.append(f"URL is not https: {loc}")
        key = norm(loc)
        if key in seen:
            problems.append(f"Duplicate URL: {loc}")
        seen.add(key)
        if lastmod and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", lastmod):
            problems.append(f"lastmod is not YYYY-MM-DD for {loc} ({lastmod}).")
    if not locs:
        problems.append("The sitemap has no URLs.")
    return locs, problems


def schema_types(node: object, found: set[str]) -> None:
    if isinstance(node, dict):
        kind = node.get("@type")
        if isinstance(kind, str):
            found.add(kind)
        elif isinstance(kind, list):
            found.update(item for item in kind if isinstance(item, str))
        for value in node.values():
            schema_types(value, found)
    elif isinstance(node, list):
        for item in node:
            schema_types(item, found)


def breadcrumb_lists(nodes: list[object]) -> list[dict]:
    found: list[dict] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            kind = node.get("@type")
            kinds = kind if isinstance(kind, list) else [kind]
            if "BreadcrumbList" in kinds:
                found.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for node in nodes:
        walk(node)
    return found


def breadcrumb_matches(nodes: list[object], page_url: str) -> bool:
    home = norm(SITE + "/")
    blog = norm(SITE + "/blog.html")
    page = norm(page_url)
    for node in breadcrumb_lists(nodes):
        raw = node.get("itemListElement") or []
        if isinstance(raw, dict):
            raw = [raw]
        by_pos: dict[int, tuple[str, str]] = {}
        for el in raw:
            if not isinstance(el, dict):
                continue
            name = str(el.get("name") or "").strip()
            item = el.get("item")
            if isinstance(item, dict):
                item = item.get("@id") or item.get("id") or ""
            item_url = norm(str(item)) if item else ""
            try:
                pos = int(el.get("position"))
            except (TypeError, ValueError):
                continue
            by_pos[pos] = (name, item_url)
        if 1 not in by_pos or 2 not in by_pos or 3 not in by_pos:
            continue
        name1, item1 = by_pos[1]
        name2, item2 = by_pos[2]
        name3, item3 = by_pos[3]
        if (
            name1.lower() == "home"
            and item1 == home
            and name2.lower() == "insights"
            and item2 == blog
            and name3
            and item3 == page
        ):
            return True
    return False


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self._title_done = False
        self.description = ""
        self.canonical: str | None = None
        self.hrefs: list[str] = []
        self.resource_hrefs: list[str] = []
        self.images: list[tuple[str, str | None]] = []
        self._in_card = False
        self._card_href: str | None = None
        self.blog_cards: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key.lower(): (value or "") for key, value in attrs}
        if tag == "title" and not self._title_done:
            self._in_title = True
            return
        if tag == "meta" and attr.get("name", "").lower() == "description" and not self.description:
            self.description = attr.get("content", "")
        if tag == "link":
            rel = attr.get("rel", "").lower().split()
            href = attr.get("href", "")
            if "canonical" in rel and href and self.canonical is None:
                self.canonical = href
            if href and any(token in rel for token in ("stylesheet", "icon", "preload")):
                self.resource_hrefs.append(href)
        if tag == "a":
            href = attr.get("href", "")
            if href:
                self.hrefs.append(href)
            classes = attr.get("class", "")
            if self._in_card and self._card_href is None and href:
                self._card_href = href
        if tag == "article" and "blog-card" in attr.get("class", "").split():
            self._in_card = True
            self._card_href = None
        if tag == "img":
            src = attr.get("src", "")
            alt = attr.get("alt") if "alt" in attr else None
            self.images.append((src, alt))
        if tag == "script":
            src = attr.get("src", "")
            if src:
                self.resource_hrefs.append(src)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title" and self._in_title:
            self._in_title = False
            self._title_done = True
        if tag == "article" and self._in_card:
            if self._card_href:
                self.blog_cards.append(self._card_href)
            self._in_card = False
            self._card_href = None

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data


@dataclass
class PageFacts:
    title: str = ""
    description: str = ""
    canonical: str | None = None
    types: set[str] = field(default_factory=set)
    has_breadcrumb: bool = False
    breadcrumb_ok: bool = False
    json_errors: list[str] = field(default_factory=list)
    missing_alt: list[str] = field(default_factory=list)
    empty_alt: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    blog_cards: list[str] = field(default_factory=list)
    truncated: bool = False


def analyse_html(html: str, page_url: str, truncated: bool = False) -> PageFacts:
    parser = _PageParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:  # a broken page should be reported, not crash the run
        facts = PageFacts(truncated=truncated)
        facts.json_errors.append(f"The HTML could not be parsed ({exc}).")
        return facts

    nodes: list[object] = []
    errors: list[str] = []
    for match in JSONLD_RE.findall(html):
        raw = match.strip()
        if not raw:
            errors.append("A schema block is empty.")
            continue
        try:
            nodes.append(json.loads(raw))
        except json.JSONDecodeError:
            errors.append("A schema block is not valid JSON.")
    types: set[str] = set()
    for node in nodes:
        schema_types(node, types)
    lists = breadcrumb_lists(nodes)

    def internal(href: str) -> str | None:
        href = href.strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
            return None
        absolute = urljoin(page_url, href)
        absolute = urlunsplit((*urlsplit(absolute)[:4], ""))
        if host_of(absolute) != HOST:
            return None
        return absolute

    links: list[str] = []
    for href in parser.hrefs + parser.resource_hrefs:
        absolute = internal(href)
        if absolute:
            links.append(absolute)
    cards: list[str] = []
    for href in parser.blog_cards:
        absolute = internal(href)
        if absolute:
            cards.append(absolute)

    missing_alt: list[str] = []
    empty_alt: list[str] = []
    for src, alt in parser.images:
        label = src or "(image with no src)"
        if alt is None:
            missing_alt.append(label)
        elif not alt.strip():
            empty_alt.append(label)

    canonical = None
    if parser.canonical:
        canonical = norm(urljoin(page_url, parser.canonical.strip()))

    return PageFacts(
        title=collapse(parser.title),
        description=collapse(parser.description),
        canonical=canonical,
        types=types,
        has_breadcrumb=bool(lists),
        breadcrumb_ok=breadcrumb_matches(nodes, page_url) if lists else False,
        json_errors=errors,
        missing_alt=missing_alt,
        empty_alt=empty_alt,
        links=links,
        blog_cards=cards,
        truncated=truncated,
    )


@dataclass
class FetchResult:
    first_status: int | None
    final_status: int | None
    final_url: str
    body: bytes
    headers: dict[str, str]
    error: str | None = None


def _raw(url: str, read_body: bool) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    response = OPENER.open(request, timeout=TIMEOUT)
    try:
        status = int(response.status)
        headers = {key.lower(): value for key, value in response.headers.items()}
        body = response.read(MAX_BODY) if read_body else b""
        return status, headers, body
    finally:
        response.close()


def fetch(url: str, read_body: bool) -> FetchResult:
    current = url
    first: int | None = None
    seen: set[str] = set()
    last_error = "The page could not be reached."
    for attempt in range(2):
        try:
            hop_url = current
            for _hop in range(6):
                if hop_url in seen:
                    return FetchResult(first, None, hop_url, b"", {}, "The URL redirected in a loop.")
                seen.add(hop_url)
                status, headers, body = _raw(hop_url, read_body)
                if first is None:
                    first = status
                if status in REDIRECTS:
                    location = headers.get("location", "").strip()
                    if not location:
                        return FetchResult(first, status, hop_url, body, headers, "A redirect had no destination.")
                    hop_url = urljoin(hop_url, location)
                    continue
                return FetchResult(first, status, hop_url, body, headers, None)
            return FetchResult(first, None, hop_url, b"", {}, "The URL redirected too many times.")
        except Exception as exc:
            last_error = str(exc)
            if attempt == 0:
                seen.clear()
                first = None
                current = url
                continue
    return FetchResult(None, None, url, b"", {}, last_error)


def decode_body(result: FetchResult) -> str:
    charset = "utf-8"
    match = re.search(r"charset=([\w.-]+)", result.headers.get("content-type", ""), re.I)
    if match:
        charset = match.group(1)
    try:
        return result.body.decode(charset, "replace")
    except LookupError:
        return result.body.decode("utf-8", "replace")


def read_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8", errors="replace")


@dataclass
class Note:
    section: str
    path: str
    detail: str
    source: str


def cap(lines: list[str], limit: int = 20) -> list[str]:
    if len(lines) <= limit:
        return lines
    extra = len(lines) - limit
    return lines[:limit] + [f"- And {extra} more."]


def render_notes(notes: list[Note]) -> dict[str, list[str]]:
    grouped: dict[tuple[str, str, str], set[str]] = {}
    order: list[tuple[str, str, str]] = []
    for note in notes:
        key = (note.section, note.path, note.detail)
        if key not in grouped:
            grouped[key] = set()
            order.append(key)
        grouped[key].add(note.source)
    sections: dict[str, list[str]] = {}
    for key in order:
        section, path, detail = key
        sources = grouped[key]
        if sources == {"live", "repo"}:
            where = "Live and repository"
        elif "live" in sources:
            where = "Live site"
        else:
            where = "Repository"
        bullet = f"- {where}: `{path}` — {detail}"
        sections.setdefault(section, []).append(bullet)
    for section, lines in sections.items():
        lines.sort()
        sections[section] = cap(lines)
    return sections


def section_block(title: str, lines: list[str], empty: str) -> list[str]:
    block = [f"## {title}", ""]
    block.extend(lines or [empty])
    block.append("")
    return block


def run(root: Path, site: str, local_only: bool = False) -> str:
    global SITE, HOST
    SITE = site.rstrip("/")
    HOST = host_of(SITE) or HOST
    root = root.resolve()
    now = datetime.now(timezone.utc)
    stamp = f"{now.day} {now.strftime('%B %Y')}, {now.strftime('%H:%M')} UTC"

    notes: list[Note] = []
    fine: list[str] = []

    def add(section: str, path: str, detail: str, source: str) -> None:
        notes.append(Note(section, path, detail, source))

    repo_robots = read_file(root / "robots.txt")
    repo_llms = read_file(root / "llms.txt")
    repo_sitemap = read_file(root / "sitemap.xml")
    live_robots = live_llms = live_sitemap = None
    live_robots_status = live_llms_status = live_sitemap_status = None

    if not local_only:
        log("Fetching sitemap.xml, robots.txt and llms.txt")
        fetched = {
            path: fetch(SITE + path, read_body=True)
            for path in ("/sitemap.xml", "/robots.txt", "/llms.txt")
        }
        live_sitemap_status = fetched["/sitemap.xml"]
        live_robots_status = fetched["/robots.txt"]
        live_llms_status = fetched["/llms.txt"]
        if live_sitemap_status.final_status == 200:
            live_sitemap = decode_body(live_sitemap_status)
        if live_robots_status.final_status == 200:
            live_robots = decode_body(live_robots_status)
        if live_llms_status.final_status == 200:
            live_llms = decode_body(live_llms_status)

    def file_section(
        label: str,
        filename: str,
        repo_text: str | None,
        live_text: str | None,
        live_result: FetchResult | None,
        validate,
    ) -> bool:
        ok = True
        if repo_text is None:
            add("files", filename, f"{label} is missing from the repository.", "repo")
            ok = False
        else:
            for problem in validate(repo_text):
                add("files", filename, problem, "repo")
                ok = False
        if local_only:
            return ok and repo_text is not None
        if live_result is None or live_result.final_status != 200 or live_text is None:
            status = "no response" if live_result is None or live_result.final_status is None else f"HTTP {live_result.final_status}"
            error = f" ({live_result.error})" if live_result and live_result.error else ""
            add("files", filename, f"The live file did not return 200 ({status}){error}.", "live")
            ok = False
        else:
            for problem in validate(live_text):
                add("files", filename, problem, "live")
                ok = False
            if repo_text is not None and collapse(repo_text) != collapse(live_text):
                # URL-list comparison for the sitemap is reported separately.
                if filename != "sitemap.xml":
                    add("files", filename, "The live copy and the repository copy are different.", "live")
                    ok = False
        return ok

    robots_ok = file_section("robots.txt", "robots.txt", repo_robots, live_robots, live_robots_status, validate_robots)
    llms_ok = file_section("llms.txt", "llms.txt", repo_llms, live_llms, live_llms_status, validate_llms)

    repo_locs, repo_sitemap_problems = ([], ["sitemap.xml is missing from the repository."]) if repo_sitemap is None else parse_sitemap(repo_sitemap)
    live_locs: list[str] = []
    live_sitemap_problems: list[str] = []
    if not local_only:
        if live_sitemap is None:
            status = "no response" if live_sitemap_status is None or live_sitemap_status.final_status is None else f"HTTP {live_sitemap_status.final_status}"
            live_sitemap_problems.append(f"The live sitemap did not return 200 ({status}).")
        else:
            live_locs, live_sitemap_problems = parse_sitemap(live_sitemap)

    for problem in repo_sitemap_problems:
        add("files", "sitemap.xml", problem, "repo")
    for problem in live_sitemap_problems:
        add("files", "sitemap.xml", problem, "live")

    sitemap_ok = not repo_sitemap_problems and (local_only or not live_sitemap_problems)
    if repo_locs and live_locs:
        repo_set = {norm(item) for item in repo_locs}
        live_set = {norm(item) for item in live_locs}
        only_repo = sorted(repo_set - live_set)
        only_live = sorted(live_set - repo_set)
        for item in only_repo[:10]:
            add("files", short_path(item), "This URL is in the repository sitemap but not in the live sitemap.", "repo")
        for item in only_live[:10]:
            add("files", short_path(item), "This URL is in the live sitemap but not in the repository sitemap.", "live")
        if only_repo or only_live:
            sitemap_ok = False
        elif sitemap_ok:
            fine.append(f"The sitemap is valid, and the live copy lists the same {len(repo_set)} URLs as the repository.")
    elif sitemap_ok and repo_locs and local_only:
        fine.append(f"The repository sitemap is valid ({len(repo_locs)} URLs). The live sitemap was not requested.")

    if robots_ok and repo_robots is not None and (local_only or (live_robots is not None and collapse(repo_robots) == collapse(live_robots))):
        if local_only:
            fine.append("robots.txt is valid and points at the sitemap. The live file was not requested.")
        else:
            fine.append("robots.txt is valid, points at the sitemap, and the live copy matches the repository.")
    if llms_ok and repo_llms is not None and (local_only or (live_llms is not None and collapse(repo_llms) == collapse(live_llms))):
        if local_only:
            fine.append("llms.txt is present. The live file was not requested.")
        else:
            fine.append("llms.txt is present, and the live copy matches the repository.")

    urls: list[str] = []
    seen_urls: set[str] = set()

    def queue(url: str) -> None:
        if host_of(url) != HOST:
            return
        key = norm(url)
        if key in seen_urls:
            return
        seen_urls.add(key)
        urls.append(url if url.startswith("http") else urljoin(SITE + "/", url))

    for loc in repo_locs + live_locs:
        queue(loc)
    blog_html = read_file(root / "blog.html")
    if blog_html:
        for card in analyse_html(blog_html, SITE + "/blog.html").blog_cards:
            queue(card)

    log(f"Checking {len(urls)} pages")
    live_pages: dict[str, FetchResult] = {}
    if not local_only and urls:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = {pool.submit(fetch, url, True): url for url in urls}
            done = 0
            for future in as_completed(futures):
                url = futures[future]
                live_pages[url] = future.result()
                done += 1
                if done % 10 == 0 or done == len(urls):
                    log(f"Fetched {done} of {len(urls)} pages")

    card_urls: set[str] = set()
    link_sources: dict[str, set[str]] = {}
    status_by_norm: dict[str, FetchResult] = {}
    non_200 = 0
    pages_with_html = 0
    blog_post_urls: set[str] = set()

    def remember_links(facts: PageFacts, source_url: str, source: str) -> None:
        for link in facts.links:
            link_sources.setdefault(norm(link), set()).add(short_path(source_url))
        for card in facts.blog_cards:
            card_urls.add(norm(card))

    for url in urls:
        path = short_path(url)
        expected = norm(url)
        rel = url_to_relpath(url)
        local_html = read_file(root / rel) if rel else None
        live_result = live_pages.get(url)

        if not local_only:
            if live_result is None or live_result.error or live_result.first_status != 200:
                non_200 += 1
                if live_result is None:
                    detail = "The live page was not fetched."
                elif live_result.error and live_result.first_status is None:
                    detail = f"The live page could not be reached ({live_result.error})."
                else:
                    status = live_result.first_status
                    extra = ""
                    if live_result.final_url and norm(live_result.final_url) != expected:
                        extra = f" It pointed at {short_path(live_result.final_url)}."
                    if rel and (root / rel).is_file() and status == 404:
                        extra += " The file is in the repository. If you just merged it, wait for GitHub Pages and run this check again."
                    detail = f"The sitemap URL returned HTTP {status}, not 200.{extra}"
                add("status", path, detail, "live")
            else:
                status_by_norm[expected] = live_result

        if local_html is None and rel:
            add("status", path, "This URL is listed, but the file is not in the repository.", "repo")

        sources: list[tuple[str, str | None]] = [("repo", local_html)]
        if not local_only:
            live_html = decode_body(live_result) if live_result and live_result.final_status == 200 and live_result.body else None
            sources.append(("live", live_html))
        for source, html in sources:
            if html is None:
                continue
            pages_with_html += 1
            facts = analyse_html(html, url, truncated=bool(live_result and source == "live" and len(live_result.body) >= MAX_BODY))
            remember_links(facts, url, source)
            if "BlogPosting" in facts.types:
                blog_post_urls.add(expected)
            if facts.truncated:
                add("status", path, "The HTML was cut off because the page is very large, so this check may be incomplete.", source)
            if not facts.title:
                add("meta", path, "The title is missing.", source)
            elif len(facts.title) < TITLE_MIN or len(facts.title) > TITLE_MAX:
                kind = "short" if len(facts.title) < TITLE_MIN else "long"
                shown = facts.title if len(facts.title) <= 90 else facts.title[:87] + "..."
                add("length", path, f"The title is {kind} at {len(facts.title)} characters (aim for {TITLE_MIN} to {TITLE_MAX}). \"{shown}\"", source)
            if not facts.description:
                add("meta", path, "The meta description is missing.", source)
            elif len(facts.description) < DESC_MIN or len(facts.description) > DESC_MAX:
                kind = "short" if len(facts.description) < DESC_MIN else "long"
                add("length", path, f"The meta description is {kind} at {len(facts.description)} characters (aim for {DESC_MIN} to {DESC_MAX}).", source)
            if facts.canonical is None:
                add("canonical", path, "The canonical link is missing.", source)
            elif facts.canonical != expected:
                add("canonical", path, f"The canonical link is `{short_path(facts.canonical)}`, not this page.", source)
            for error in facts.json_errors:
                add("schema", path, error, source)
            for src in facts.missing_alt:
                add("alt", path, f"An image has no alt text ({src}).", source)
            for src in facts.empty_alt:
                add("alt", path, f"An image has empty alt text ({src}).", source)

    # Cards found on a fetched blog index that were not in the sitemap or the
    # repository blog index. Rare. Checked so a new post cannot skip schema.
    missing_cards = [item for item in sorted(card_urls) if item not in seen_urls]
    if missing_cards and not local_only:
        log(f"Checking {len(missing_cards)} blog links that were not in the sitemap")
    for card_norm in missing_cards:
        # card_norm is already normalised. Recover a fetchable URL.
        fetch_url = card_norm
        path = short_path(fetch_url)
        blog_post_urls.add(card_norm)
        seen_urls.add(card_norm)
        urls.append(fetch_url)
        rel = url_to_relpath(fetch_url)
        local_html = read_file(root / rel) if rel else None
        live_html = None
        if not local_only:
            result = fetch(fetch_url, read_body=True)
            live_pages[fetch_url] = result
            if result.first_status != 200:
                add("status", path, f"A blog index link returned HTTP {result.first_status}, not 200.", "live")
            elif result.body:
                live_html = decode_body(result)
                status_by_norm[card_norm] = result
        for source, html in (("repo", local_html), ("live", live_html)):
            if not html:
                continue
            facts = analyse_html(html, fetch_url)
            remember_links(facts, fetch_url, source)
            if "BlogPosting" in facts.types:
                blog_post_urls.add(card_norm)

    # Schema is decided after every blog card is known.
    # Re-scan stored facts would require keeping them. Analyse again from files
    # and from the already fetched bodies. This second pass is local and cheap.
    def schema_for(url: str, source: str, html: str | None) -> None:
        if html is None:
            return
        facts = analyse_html(html, url)
        path = short_path(url)
        is_post = norm(url) in blog_post_urls or norm(url) in card_urls or "BlogPosting" in facts.types
        if not is_post:
            return
        if "BlogPosting" not in facts.types:
            add("schema", path, "This blog post has no BlogPosting schema.", source)
        if not facts.has_breadcrumb:
            add("schema", path, "This blog post has no BreadcrumbList schema.", source)
        elif not facts.breadcrumb_ok:
            add("schema", path, "BreadcrumbList is present, but it is not Home > Insights > this page.", source)

    checked_schema: set[tuple[str, str]] = set()
    for url in urls:
        rel = url_to_relpath(url)
        local_html = read_file(root / rel) if rel else None
        key = (norm(url), "repo")
        if key not in checked_schema:
            checked_schema.add(key)
            schema_for(url, "repo", local_html)
        if local_only:
            continue
        live_result = live_pages.get(url)
        live_html = decode_body(live_result) if live_result and live_result.final_status == 200 and live_result.body else None
        key = (norm(url), "live")
        if key not in checked_schema:
            checked_schema.add(key)
            schema_for(url, "live", live_html)

    # Internal links.
    log(f"Checking {len(link_sources)} internal links")
    link_targets = sorted(link_sources)
    if not local_only:
        to_fetch = [target for target in link_targets if target not in status_by_norm]
        if to_fetch:
            with ThreadPoolExecutor(max_workers=WORKERS) as pool:
                futures = {pool.submit(fetch, target, False): target for target in to_fetch}
                for future in as_completed(futures):
                    target = futures[future]
                    status_by_norm[target] = future.result()
        for target, sources in sorted(link_sources.items()):
            result = status_by_norm.get(target)
            linked = ", ".join(sorted(sources)[:6])
            if len(sources) > 6:
                linked += f", and {len(sources) - 6} more"
            if result is None or result.final_status != 200:
                status = "no response" if result is None or result.final_status is None else f"HTTP {result.final_status}"
                add("links", short_path(target), f"Broken internal link ({status}). Linked from {linked}.", "live")
    for target, sources in sorted(link_sources.items()):
        rel = url_to_relpath(target)
        if rel is None:
            continue
        if not (root / rel).is_file():
            linked = ", ".join(sorted(sources)[:6])
            add("links", short_path(target), f"The linked file is not in the repository ({rel}). Linked from {linked}.", "repo")

    rendered = render_notes(notes)
    blog_count = len(blog_post_urls | card_urls)
    fix_sections = {"files", "status", "meta", "canonical", "schema", "links", "alt"}
    fix_count = sum(1 for note in notes if note.section in fix_sections)
    length_count = sum(1 for note in notes if note.section == "length")

    def lines_for(*sections: str) -> list[str]:
        collected: list[str] = []
        for name in sections:
            collected.extend(rendered.get(name, []))
        return collected

    file_lines = lines_for("files")
    status_lines = lines_for("status")
    meta_lines = lines_for("meta")
    length_lines = lines_for("length")
    canonical_lines = lines_for("canonical")
    schema_lines = lines_for("schema")
    link_lines = lines_for("links")
    alt_lines = lines_for("alt")

    if fix_count == 0 and length_count == 0:
        summary = "Nothing needs attention this week."
    elif fix_count == 0:
        summary = f"No broken checks. {length_count} title or description length{'s' if length_count != 1 else ''} to tighten."
    else:
        summary = (
            f"{fix_count} thing{'s' if fix_count != 1 else ''} to fix. "
            f"{length_count} title or description length{'s' if length_count != 1 else ''} to tighten."
        )

    if local_only:
        intro = (
            f"Checked the HTML in the repository only. The live site was not requested. "
            f"{len(urls)} pages. {blog_count} blog posts. This check does not change the site."
        )
    else:
        intro = (
            f"Checked the live site at {SITE} and the HTML in the repository. "
            f"{len(urls)} pages. {blog_count} blog posts. This check does not change the site."
        )

    if not meta_lines:
        fine.append("Every checked page has a title and a meta description.")
    if not length_lines and not meta_lines:
        fine.append(f"Titles are about {TITLE_MIN} to {TITLE_MAX} characters, and meta descriptions about {DESC_MIN} to {DESC_MAX}.")
    if not canonical_lines and pages_with_html:
        fine.append("Every checked page has a canonical link that matches its own URL.")
    if not schema_lines and blog_count:
        fine.append("Every blog post has BlogPosting and a BreadcrumbList of Home > Insights > the post.")
    elif not blog_count:
        fine.append("No blog posts were found to check for BlogPosting and BreadcrumbList.")
    if not link_lines:
        fine.append("No broken internal links.")
    if not alt_lines:
        fine.append("No images with missing alt text.")
    if not status_lines and not local_only and urls:
        fine.append("Every sitemap URL returned HTTP 200.")

    parts = [
        f"# Site health check, {now.day} {now.strftime('%B %Y')}",
        "",
        intro,
        "",
        f"Ran at {stamp}.",
        "",
        "## In short",
        "",
        summary,
        "",
    ]
    parts += section_block(
        "Sitemap, robots.txt and llms.txt",
        file_lines,
        "Nothing to fix in sitemap.xml, robots.txt or llms.txt.",
    )
    if local_only:
        status_empty = "Not checked. This run did not request the live site."
    else:
        status_empty = "None. Every sitemap URL returned HTTP 200."
    parts += section_block("Pages that did not return 200", status_lines, status_empty)
    meta_and_length = meta_lines + length_lines
    length_empty = (
        f"Every checked page has a title and a meta description, within the usual length "
        f"(title about {TITLE_MIN} to {TITLE_MAX} characters, description about {DESC_MIN} to {DESC_MAX})."
    )
    parts += section_block("Titles and descriptions", meta_and_length, length_empty)
    parts += section_block(
        "Canonical links",
        canonical_lines,
        "Every checked page has a canonical link that matches its own URL.",
    )
    if blog_count:
        schema_empty = f"Every blog post has BlogPosting and a BreadcrumbList of Home > Insights > the post. {blog_count} posts checked."
    else:
        schema_empty = "No blog posts were found."
    parts += section_block("Blog schema", schema_lines, schema_empty)
    parts += section_block("Broken internal links", link_lines, "None.")
    parts += section_block("Images missing alt text", alt_lines, "None.")
    if fine:
        parts += section_block("What looks fine", [f"- {item}" for item in fine], "Nothing else to add.")
    parts.append("Report-id: site-health-bot")
    parts.append("<!-- site-health-bot -->")
    parts.append("")
    return "\n".join(parts)


def self_test() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "robots.txt").write_text(
            "User-agent: *\nAllow: /\n\nSitemap: https://faisalconsulting.co.uk/sitemap.xml\n",
            encoding="utf-8",
        )
        (root / "llms.txt").write_text("# Faisal Consulting\n\nA fractional marketing director.\n", encoding="utf-8")
        (root / "sitemap.xml").write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://faisalconsulting.co.uk/</loc></url>
  <url><loc>https://faisalconsulting.co.uk/blog.html</loc></url>
  <url><loc>https://faisalconsulting.co.uk/post.html</loc></url>
</urlset>
""",
            encoding="utf-8",
        )
        (root / "index.html").write_text(
            """<!doctype html><html><head>
<title>Faisal Mahmud | Fractional Marketing Director</title>
<meta name="description" content="Senior fractional and interim marketing leadership for businesses facing wasted spend, broken tracking or a leadership gap. UK and Europe.">
<link rel="canonical" href="https://faisalconsulting.co.uk/">
</head><body>
<a href="blog.html">Insights</a>
<img src="logo.svg" alt="Faisal Consulting">
</body></html>
""",
            encoding="utf-8",
        )
        (root / "logo.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'></svg>", encoding="utf-8")
        (root / "blog.html").write_text(
            """<!doctype html><html><head>
<title>Marketing Insights and Brand Teardowns | Faisal Mahmud</title>
<meta name="description" content="Evidence-led brand marketing teardowns and practical insights on performance marketing, measurement, conversion and agency accountability.">
<link rel="canonical" href="https://faisalconsulting.co.uk/blog.html">
</head><body>
<article class="blog-card"><a href="post.html"><h2>A post</h2></a></article>
<article class="blog-card"><a href="bare.html"><h2>Bare</h2></a></article>
<a href="gone.html">Missing</a>
</body></html>
""",
            encoding="utf-8",
        )
        (root / "bare.html").write_text(
            """<!doctype html><html><head>
<title>A bare post that still needs schema and a proper description</title>
<meta name="description" content="A plain note about a bare post, long enough to sit inside the usual meta description range for this check.">
<link rel="canonical" href="https://faisalconsulting.co.uk/bare.html">
</head><body><p>No schema here.</p></body></html>
""",
            encoding="utf-8",
        )
        long_title = "This title is deliberately much longer than sixty characters so the check flags it"
        (root / "post.html").write_text(
            f"""<!doctype html><html><head>
<title>{long_title}</title>
<meta name="description" content="Too short.">
<link rel="canonical" href="https://faisalconsulting.co.uk/other.html">
<script type="application/ld+json">{{"@context":"https://schema.org","@type":"BlogPosting","headline":"A post"}}</script>
</head><body>
<img src="logo.svg">
<a href="index.html">Home</a>
</body></html>
""",
            encoding="utf-8",
        )
        report = run(root, SITE, local_only=True)
        expected = [
            "gone.html",
            "no BreadcrumbList schema",
            "no BlogPosting schema",
            "canonical link is",
            "no alt text",
            "The title is long",
            "The meta description is short",
            "robots.txt is valid",
            "llms.txt is present",
        ]
        # blog.html is a card host, not itself a post, so it needs no BlogPosting.
        # post.html is a card target and has BlogPosting, so missing breadcrumb is reported.
        # The blog index page itself is not a blog post.
        missing = [item for item in expected if item not in report]
        if missing:
            raise SystemExit("Self-test failed. Missing from the report:\n- " + "\n- ".join(missing) + "\n\n" + report)
        if "The sitemap is not valid XML" in report:
            raise SystemExit("Self-test failed. A valid sitemap was reported as invalid.\n" + report)
        if "index.html" in report and "canonical" in report.split("index.html", 1)[-1][:80]:
            raise SystemExit("Self-test failed. The homepage canonical was flagged.\n" + report)
        robots_problems = validate_robots("User-agent: *\nDisallow: /\n")
        if not any("Sitemap" in item for item in robots_problems):
            raise SystemExit("Self-test failed. A robots file with no Sitemap line was accepted.")
        bad_locs, bad_problems = parse_sitemap("<urlset></urlset>")
        if bad_locs or not bad_problems:
            raise SystemExit("Self-test failed. An empty sitemap was accepted.")
        good = analyse_html(
            """<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"BreadcrumbList","itemListElement":[
{"@type":"ListItem","position":1,"name":"Home","item":"https://faisalconsulting.co.uk/"},
{"@type":"ListItem","position":2,"name":"Insights","item":"https://faisalconsulting.co.uk/blog.html"},
{"@type":"ListItem","position":3,"name":"A post","item":"https://faisalconsulting.co.uk/post.html"}]}
</script>
</head></html>""",
            SITE + "/post.html",
        )
        if not good.breadcrumb_ok:
            raise SystemExit("Self-test failed. A valid Home > Insights breadcrumb was rejected.")
    print("Site health self-test passed.", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check faisalconsulting.co.uk and write a plain-English report.")
    parser.add_argument("--root", default=".", help="Repository root. Defaults to the current directory.")
    parser.add_argument("--site", default=SITE, help="Live site origin.")
    parser.add_argument("--output", default="", help="Also write the report to this file.")
    parser.add_argument("--local-only", action="store_true", help="Do not request the live site.")
    parser.add_argument("--self-test", action="store_true", help="Run the offline self-test and exit.")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    report = run(Path(args.root), args.site, local_only=args.local_only)
    if args.output:
        Path(args.output).write_text(report, encoding="utf-8")
    sys.stdout.write(report)
    if not report.endswith("\n"):
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(0)
