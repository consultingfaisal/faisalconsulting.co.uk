#!/usr/bin/env python3
"""Turn the next queued row in content/calendar.csv into a blog draft.

The draft matches the current insight-post template (header, footer, brand
files, BlogPosting and BreadcrumbList). It does not publish. A pull request
is opened by the GitHub Action. The post goes live only when that request
is merged.

If GEMINI_API_KEY is set, the body is written with Google's Gemini API.
Busy or rate-limited models are retried. If every try fails, the run stops
and the calendar row stays queued. No placeholder pull request is opened.
If the key is not set, the page is a structure with [FAISAL TO ADD] placeholders.
"""

from __future__ import annotations

import argparse
import csv
import html
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

SITE = "https://faisalconsulting.co.uk"
REFERENCE_POST = "blog-hiring-fractional-marketing-director.html"
CALENDAR_NAME = Path("content") / "calendar.csv"
EXPECTED_COLUMNS = ["date", "topic", "target_query", "status", "notes"]
INSIGHTS_MARKER = '<h2 class="blog-section-title blog-section-title--second">Insights</h2>'
MODELS = ("gemini-3.8-flash", "gemini-flash-latest", "gemini-3.5-flash-lite")
# Five tries. The four waits between them add up to 10 minutes.
MAX_MODEL_ATTEMPTS = 5
RETRY_BACKOFF_SECONDS = (30, 90, 180, 300)
MODEL_RULES = (
    "Write in UK English. Use short sentences. No hype. "
    "No invented figures, clients, quotes or results. "
    "Anything that needs a real number becomes a visible [FAISAL TO ADD] marker."
)
EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF]")


@dataclass
class Section:
    heading: str = ""
    paragraphs: list[str] = field(default_factory=list)
    bullets: list[str] = field(default_factory=list)


@dataclass
class Draft:
    title: str
    description: str
    excerpt: str
    sections: list[Section]


@dataclass
class Built:
    filename: str
    slug: str
    title: str
    topic: str
    query: str
    notes: str
    calendar_date: str
    writer: str
    writer_note: str
    html: str
    card: str
    markers: int
    row_index: int


def log(message: str) -> None:
    print(message, file=sys.stderr)


def collapse(text: str) -> str:
    return " ".join(text.split())


def esc_text(value: str) -> str:
    return html.escape(value, quote=False)


def esc_attr(value: str) -> str:
    return html.escape(value, quote=True)


def redact(text: str, key: str = "") -> str:
    if key:
        text = text.replace(key, "REDACTED")
    text = re.sub(r"key=[^&\s\"']+", "key=REDACTED", text)
    text = re.sub(r"AIza[0-9A-Za-z_-]{10,}", "REDACTED", text)
    return text


def slugify(text: str) -> str:
    text = text.replace("'", "").replace("’", "")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    text = re.sub(r"-{2,}", "-", text)
    return text[:70].strip("-") or "post"


def scrub_figures(text: str) -> str:
    text = re.sub(
        r"[$£€]\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:million|billion|thousand|k|m|bn))?",
        "[FAISAL TO ADD]",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"\b\d+(?:\.\d+)?\s?(?:%|percent|per cent)(?!\w)",
        "[FAISAL TO ADD]",
        text,
        flags=re.I,
    )
    text = re.sub(r"(?:\[\s*FAISAL TO ADD\s*\]\s*){2,}", "[FAISAL TO ADD]", text)
    return text


def plain(value: object, limit: int = 600) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("**", "").replace("__", "").replace("`", "")
    text = text.replace("!", ".")
    text = re.sub(r"\.{2,}", ".", text)
    text = EMOJI_RE.sub("", text)
    text = scrub_figures(text)
    text = collapse(text)
    if len(text) > limit:
        cut = text[: limit - 1].rsplit(" ", 1)[0]
        text = (cut or text[: limit - 1]).rstrip(".,;:") + "."
    return text.strip()


def fit_description(text: str, fallback: str) -> str:
    text = collapse(text) or collapse(fallback)
    if len(text) > 160:
        cut = text[:157].rsplit(" ", 1)[0].rstrip(".,;:")
        text = (cut or text[:157]).rstrip() + "."
        if len(text) > 160:
            text = text[:159].rstrip() + "."
    return text


def placeholder_description(topic: str) -> str:
    base = topic.strip().rstrip(".")
    suffix = " A practical note from Faisal Mahmud, fractional marketing director."
    text = base + "."
    if len(text) + len(suffix) <= 160:
        text += suffix
    return fit_description(text, text)


def uk_date(value: date) -> str:
    return f"{value.day} {value.strftime('%B %Y')}"


def json_for_script(payload: object) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return raw.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def read_calendar(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise SystemExit(
            f"Missing {path}. The first line must be: {','.join(EXPECTED_COLUMNS)}"
        )
    text = path.read_text(encoding="utf-8-sig")
    if not text.strip():
        raise SystemExit(f"{path} is empty. The first line must be: {','.join(EXPECTED_COLUMNS)}")
    reader = csv.DictReader(io.StringIO(text))
    fields = [(name or "").strip() for name in (reader.fieldnames or [])]
    if fields != EXPECTED_COLUMNS:
        raise SystemExit(
            f"{path} must use these columns, in this order: {','.join(EXPECTED_COLUMNS)}. "
            f"Found: {','.join(fields) or '(none)'}."
        )
    rows: list[dict[str, str]] = []
    for row in reader:
        rows.append({column: collapse(row.get(column) or "") for column in EXPECTED_COLUMNS})
    return rows


def write_calendar(path: Path, rows: list[dict[str, str]]) -> None:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=EXPECTED_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({column: row.get(column, "") for column in EXPECTED_COLUMNS})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(buffer.getvalue(), encoding="utf-8")


def next_queued(rows: list[dict[str, str]]) -> tuple[int, dict[str, str]] | None:
    queued = [(index, row) for index, row in enumerate(rows) if row["status"].lower() == "queued"]
    if not queued:
        return None

    def sort_key(item: tuple[int, dict[str, str]]) -> tuple[int, date, int]:
        index, row = item
        try:
            parsed = date.fromisoformat(row["date"])
        except ValueError:
            return (0, date.min, index)
        return (1, parsed, index)

    queued.sort(key=sort_key)
    index, row = queued[0]
    line = index + 2
    try:
        date.fromisoformat(row["date"])
    except ValueError:
        raise SystemExit(
            f"Line {line} of the calendar is queued, but the date is not YYYY-MM-DD "
            f"({row['date'] or 'blank'}). No draft was created."
        )
    if not row["topic"]:
        raise SystemExit(f"Line {line} of the calendar is queued, but the topic is empty. No draft was created.")
    return index, row


def unique_filename(root: Path, slug: str) -> str:
    candidate = f"blog-{slug}.html"
    if not (root / candidate).exists():
        return candidate
    for number in range(2, 21):
        candidate = f"blog-{slug}-{number}.html"
        if not (root / candidate).exists():
            return candidate
    raise SystemExit(f"Too many existing files for the slug {slug}. No draft was created.")


def load_chrome(reference_html: str) -> dict[str, str]:
    text = reference_html.replace("\r\n", "\n")
    preload = re.search(r'^<link rel="preload" href="archivo-latin\.woff2"[^\n]*$', text, re.M)
    assets = re.search(r'^<link rel="stylesheet" href="style\.css\?v=[^"]+"[^\n]*$', text, re.M)
    body = re.search(r"<body>\n.*?</header>\n\n", text, re.S)
    footer = re.search(r'<footer class="site-footer wrap">.*\Z', text, re.S)
    missing = [
        name
        for name, match in (
            ("font preload", preload),
            ("stylesheet", assets),
            ("header", body),
            ("footer", footer),
        )
        if not match
    ]
    if missing:
        raise SystemExit(
            f"Could not read the blog template in {REFERENCE_POST} "
            f"({', '.join(missing)}). No draft was created."
        )
    assert preload and assets and body and footer
    return {
        "preload": preload.group(0),
        "assets": assets.group(0),
        "body_header": body.group(0),
        "footer": footer.group(0).rstrip() + "\n",
    }


RELATED_PAGES = [
    ("ir35-fractional-marketing.html", "IR35 and Fractional Marketing Directors, Explained", ["ir35"]),
    ("fractional-marketing-director-cost.html", "How Much Does a Fractional Marketing Director Cost?", ["cost", "price", "fee", "fees", "day rate"]),
    ("fractional-marketing-director-london.html", "Fractional Marketing Director for London Businesses", ["london"]),
    ("interim-marketing-director.html", "Interim Marketing Director", ["interim"]),
    ("fractional-cmo-spain.html", "Fractional CMO in Spain", ["alicante", "valencia", "spain", "spanish"]),
    ("fractional-cmo.html", "Fractional CMO for UK and European Brands", ["cmo"]),
    ("marketing-performance-audit.html", "Marketing Performance Audit: What It Covers", ["audit", "attribution", "tracking", "measurement"]),
    ("healthcare-marketing.html", "Healthcare Marketing Consultant", ["healthcare", "nhs"]),
    ("fmcg-marketing.html", "FMCG Marketing Consultant", ["fmcg"]),
    ("hospitality-marketing.html", "Hospitality Marketing Consultant", ["hospitality", "hotel", "restaurant"]),
    ("charity-marketing.html", "Charity Marketing Consultant", ["charity", "nonprofit", "non-profit"]),
    ("founder-guidance.html", "Early-Stage Founder Guidance", ["founder"]),
    ("employer-branding-spain.html", "Employer Branding Consultant Spain", ["employer brand", "employer branding"]),
    ("international-talent-marketing.html", "International Talent Marketing for Spanish Companies", ["talent", "recruitment"]),
    ("costa-blanca-marketing.html", "Costa Blanca & Valencia Marketing Consultant", ["costa blanca"]),
    ("marketing-consultant.html", "Marketing Consultant, UK and Europe", ["consultant"]),
    ("resources.html", "Free Marketing Templates and Checklists", ["checklist", "template"]),
]
DEFAULT_RELATED = [
    ("services.html", "Marketing Services and Engagement Models"),
    ("case-studies.html", "Marketing Case Studies"),
]


def choose_related(root: Path, topic: str, query: str, notes: str) -> list[tuple[str, str]]:
    haystack = f"{topic} {query} {notes}".lower()
    scored: list[tuple[int, int, str, str]] = []
    for index, (href, label, keywords) in enumerate(RELATED_PAGES):
        if not (root / href).is_file():
            continue
        score = sum(1 for keyword in keywords if keyword in haystack)
        if score:
            scored.append((score, -index, href, label))
    scored.sort(reverse=True)
    chosen: list[tuple[str, str]] = []
    seen: set[str] = set()
    for _score, _index, href, label in scored:
        if href in seen:
            continue
        chosen.append((href, label))
        seen.add(href)
        if len(chosen) == 2:
            return chosen
    for href, label in DEFAULT_RELATED:
        if href in seen or not (root / href).is_file():
            continue
        chosen.append((href, label))
        seen.add(href)
        if len(chosen) == 2:
            break
    return chosen


def related_html(pairs: list[tuple[str, str]]) -> str:
    if not pairs:
        return ""
    links = [
        f'<a href="{esc_attr(href)}">{esc_text(label)}</a>'
        for href, label in pairs
    ]
    separator = ' <span aria-hidden="true">·</span> '
    return f'<nav class="related-links wrap" aria-label="Related"><p>{separator.join(links)}</p></nav>'


def sections_html(sections: list[Section]) -> str:
    blocks: list[str] = []
    for section in sections:
        parts: list[str] = []
        if section.heading:
            parts.append(f"    <h2>{esc_text(section.heading)}</h2>")
        for paragraph in section.paragraphs:
            parts.append(f"    <p>{esc_text(paragraph)}</p>")
        if section.bullets:
            items = "\n".join(f"      <li>{esc_text(item)}</li>" for item in section.bullets)
            parts.append(f"    <ul>\n{items}\n    </ul>")
        if parts:
            blocks.append("\n".join(parts))
    return "\n\n".join(blocks)


def render_page(
    chrome: dict[str, str],
    filename: str,
    title: str,
    description: str,
    excerpt: str,
    display_date: str,
    iso_date: str,
    sections: list[Section],
    related: list[tuple[str, str]],
) -> str:
    canonical = f"{SITE}/{filename}"
    og_description = excerpt if 40 <= len(excerpt) <= 200 else description
    posting = {
        "@context": "https://schema.org",
        "@type": "BlogPosting",
        "headline": title,
        "description": description,
        "url": canonical,
        "mainEntityOfPage": {"@type": "WebPage", "@id": canonical},
        "datePublished": iso_date,
        "dateModified": iso_date,
        "author": {
            "@type": "Person",
            "@id": f"{SITE}/#faisal",
            "name": "Faisal Mahmud",
            "jobTitle": "Fractional Marketing Director",
            "url": f"{SITE}/",
        },
        "publisher": {
            "@type": "Organization",
            "@id": f"{SITE}/#org",
            "name": "Faisal Consulting",
            "url": f"{SITE}/",
            "logo": {"@type": "ImageObject", "url": f"{SITE}/logo.png"},
        },
        "image": f"{SITE}/social-preview.jpg",
    }
    breadcrumb = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{SITE}/"},
            {"@type": "ListItem", "position": 2, "name": "Insights", "item": f"{SITE}/blog.html"},
            {"@type": "ListItem", "position": 3, "name": title, "item": canonical},
        ],
    }
    head = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1">
<meta name="description" content="{esc_attr(description)}">
<title>{esc_text(title)}</title>
<link rel="canonical" href="{esc_attr(canonical)}">
<meta property="og:title" content="{esc_attr(title)}">
<meta property="og:description" content="{esc_attr(og_description)}">
<meta property="og:image" content="{SITE}/social-preview.jpg">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:url" content="{esc_attr(canonical)}">
<meta property="og:type" content="article">
{chrome["preload"]}
{chrome["assets"]}


<script type="application/ld+json">{json_for_script(posting)}</script>
<script type="application/ld+json">
{json_for_script(breadcrumb)}
</script>
</head>
"""
    article = sections_html(sections)
    nav = related_html(related)
    main = f"""<main id="main-content">
  <section class="hero wrap cs-hero">
    <p class="back-link"><a href="blog.html">&larr; Insights</a></p>
    <h1>{esc_text(title)}</h1>
    <p class="hero-sub">{esc_text(display_date)}</p>
  </section>

  <article class="blog-post wrap">
{article}
  </article>
{nav}
</main>

"""
    return head + chrome["body_header"] + main + chrome["footer"]


def card_html(filename: str, display_date: str, title: str, excerpt: str) -> str:
    return (
        "    <article class=\"blog-card\">\n"
        f"      <a href=\"{esc_attr(filename)}\">\n"
        f"        <p class=\"blog-card-date\">{esc_text(display_date)}</p>\n"
        f"        <h2>{esc_text(title)}</h2>\n"
        f"        <p>{esc_text(excerpt)}</p>\n"
        "      </a>\n"
        "    </article>"
    )


def insert_card(blog_html: str, card: str) -> str:
    if blog_html.count(INSIGHTS_MARKER) != 1:
        raise SystemExit("Could not find the Insights heading in blog.html. No files were changed.")
    return blog_html.replace(INSIGHTS_MARKER, INSIGHTS_MARKER + "\n" + card, 1)


def insert_sitemap(sitemap_xml: str, filename: str, lastmod: str) -> str:
    loc = f"{SITE}/{filename}"
    if loc in sitemap_xml:
        raise SystemExit(f"{loc} is already in the sitemap. No files were changed.")
    if sitemap_xml.count("</urlset>") != 1:
        raise SystemExit("sitemap.xml does not have one closing urlset tag. No files were changed.")
    line = (
        f"  <url><loc>{xml_escape(loc)}</loc>"
        f"<lastmod>{lastmod}</lastmod><priority>0.6</priority></url>\n"
    )
    return sitemap_xml.replace("</urlset>", line + "</urlset>", 1)


def placeholder_draft(topic: str, query: str) -> Draft:
    sections = [
        Section(paragraphs=["[FAISAL TO ADD]"]),
        Section(heading=topic, paragraphs=["[FAISAL TO ADD]"]),
    ]
    if query and query.lower() != topic.lower():
        sections.append(Section(heading=query, paragraphs=["[FAISAL TO ADD]"]))
    return Draft(
        title=topic,
        description=placeholder_description(topic),
        excerpt=topic if len(topic) <= 160 else fit_description(topic, topic),
        sections=sections,
    )


def shares_topic_word(topic: str, title: str) -> bool:
    words = [word for word in re.findall(r"[a-z0-9']+", topic.lower()) if len(word) > 3]
    if not words:
        return True
    haystack = title.lower()
    return any(word in haystack for word in words)


def build_prompt(topic: str, query: str, notes: str) -> str:
    query_line = query or "No target query was set."
    notes_line = notes or "No extra notes."
    return f"""{MODEL_RULES}

You are drafting an insight for Faisal Mahmud of Faisal Consulting. He is a fractional and interim marketing director based in Alicante, working across the UK and Europe. The reader is a founder or marketing lead. Be calm and specific. Say what to check. Do not sell.

Do not invent clients, quotations, case studies, or results. A number that only structures a sentence, such as "two questions", may stay. Any statistic, fee, percentage, client count, or result must be written as [FAISAL TO ADD]. Do not include URLs. Do not mention that you are a model. Do not use emoji.

Topic: {topic}
Target search query: {query_line}
Notes from the calendar: {notes_line}

Write 4 to 6 short sections, under 500 words in total. The opening section has an empty heading. At least one later heading uses words from the topic. Return JSON only, with this shape:
{{
  "title": "based on the topic",
  "meta_description": "120 to 160 characters",
  "card_excerpt": "one short sentence under 160 characters",
  "sections": [
    {{"heading": "", "paragraphs": ["plain text"], "bullets": []}}
  ]
}}
"""


class GeminiUnavailable(Exception):
    """The API key is set, but Gemini did not return a usable draft."""


def pause(seconds: float) -> None:
    time.sleep(seconds)


def call_gemini(model: str, prompt: str, key: str, json_mode: bool) -> tuple[int, str]:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    generation: dict[str, object] = {"temperature": 0.3, "maxOutputTokens": 8192}
    if json_mode:
        generation["responseMimeType"] = "application/json"
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": generation,
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read().decode("utf-8", "replace")
            code = int(response.status)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        log(f"Gemini {model} returned HTTP {exc.code}.")
        return exc.code, redact(detail, key)[:300]
    except Exception as exc:
        log(f"Gemini {model} could not be reached.")
        return 0, redact(str(exc), key)[:300]
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return code, ""
    feedback = parsed.get("promptFeedback") or {}
    if feedback.get("blockReason"):
        return code, ""
    candidates = parsed.get("candidates") or []
    if not candidates:
        return code, ""
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "\n".join(part.get("text", "") for part in parts if isinstance(part, dict) and part.get("text"))
    return code, text


def parse_model_json(text: str) -> dict:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    data = json.loads(cleaned)
    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        raise ValueError("Gemini did not return a JSON object.")
    return data


def draft_from_model(text: str, topic: str) -> Draft:
    data = parse_model_json(text)
    title = plain(data.get("title"), 120) or topic
    if not shares_topic_word(topic, title):
        title = topic
    sections: list[Section] = []
    for raw in (data.get("sections") or [])[:8]:
        if not isinstance(raw, dict):
            continue
        paragraphs = [plain(item) for item in (raw.get("paragraphs") or [])[:4]]
        paragraphs = [item for item in paragraphs if item]
        bullets = [plain(item, 240) for item in (raw.get("bullets") or [])[:6]]
        bullets = [item for item in bullets if item]
        heading = plain(raw.get("heading"), 120)
        if paragraphs or bullets or heading:
            sections.append(Section(heading=heading, paragraphs=paragraphs, bullets=bullets))
    if not any(section.paragraphs or section.bullets for section in sections):
        raise ValueError("Gemini returned no paragraphs.")
    description = fit_description(plain(data.get("meta_description"), 220), placeholder_description(title))
    if len(description) < 70:
        description = placeholder_description(title)
    excerpt = plain(data.get("card_excerpt"), 180) or (sections[0].paragraphs[0] if sections[0].paragraphs else title)
    if len(excerpt) > 160:
        excerpt = fit_description(excerpt, title)
    return Draft(title=title, description=description, excerpt=excerpt, sections=sections)


def _unavailable(errors: list[str]) -> GeminiUnavailable:
    seen: list[str] = []
    for item in errors:
        if item not in seen:
            seen.append(item)
    detail = " ".join(seen[-8:]) if seen else "Gemini did not return a draft."
    return GeminiUnavailable(
        "Gemini did not return a draft after retrying each model up to "
        f"{MAX_MODEL_ATTEMPTS} times, with about 10 minutes of waits between tries. {detail} "
        "No pull request was opened. The calendar row is still queued, so the next run will try again."
    )


def _one_attempt(model: str, prompt: str, key: str, topic: str, call) -> tuple[str, Draft | str]:
    """Try one model once. JSON mode is retried immediately without it on HTTP 400.

    Returns ("ok", draft), ("retry", message) for 429/5xx/network, ("auth", message),
    or ("skip", message) when this model should not be retried.
    """
    for json_mode in (True, False):
        code, text = call(model, prompt, key, json_mode)
        if code == 200 and text.strip():
            try:
                return "ok", draft_from_model(text, topic)
            except (ValueError, json.JSONDecodeError):
                if json_mode:
                    continue
                return "skip", f"{model} returned text that was not a usable draft."
        if code == 400 and json_mode:
            continue
        if code in (401, 403):
            return "auth", "Gemini did not accept the API key."
        if code == 429 or code == 0 or code >= 500:
            reason = "could not be reached" if code == 0 else f"HTTP {code}"
            return "retry", f"{model} returned {reason}."
        if code == 404:
            return "skip", f"{model} was not found."
        if code:
            return "skip", f"{model} returned HTTP {code}."
    return "skip", f"{model} did not return a draft."


def write_draft(
    topic: str,
    query: str,
    notes: str,
    key: str,
    force_placeholder: bool,
    call=None,
    wait=None,
) -> tuple[Draft, str, str]:
    if force_placeholder or not key:
        return (
            placeholder_draft(topic, query),
            "placeholders",
            "No GEMINI_API_KEY is set, so this draft is a structure with [FAISAL TO ADD] placeholders.",
        )
    call = call or call_gemini
    wait = wait or pause
    prompt = build_prompt(topic, query, notes)
    retryable = set(MODELS)
    errors: list[str] = []
    for round_index in range(MAX_MODEL_ATTEMPTS):
        if round_index:
            seconds = RETRY_BACKOFF_SECONDS[round_index - 1]
            log(f"Waiting {seconds} seconds before trying Gemini again.")
            wait(seconds)
        still_retryable: set[str] = set()
        for model in MODELS:
            if model not in retryable:
                continue
            log(f"Asking {model} (try {round_index + 1} of {MAX_MODEL_ATTEMPTS}).")
            status, payload = _one_attempt(model, prompt, key, topic, call)
            if status == "ok" and isinstance(payload, Draft):
                return payload, "gemini", f"Google Gemini ({model})."
            errors.append(str(payload))
            if status == "auth":
                raise GeminiUnavailable(
                    f"{payload} No pull request was opened. The calendar row is still queued."
                )
            if status == "retry":
                still_retryable.add(model)
        retryable = still_retryable
        if not retryable:
            break
    raise _unavailable(errors)


def add_pr_note(notes: str, pr_url: str) -> str:
    cleaned = re.sub(r"\s*;?\s*PR:\s*\S+", "", notes).strip(" ;")
    piece = f"PR: {pr_url}"
    if cleaned:
        return f"{cleaned}; {piece}"
    return piece


def pull_request_body(built: Built) -> str:
    query = built.query or "(none)"
    notes = built.notes or "(none)"
    return f"""This is a draft blog post for review. It is not on the live site.

- Topic: {built.topic}
- Target query: {query}
- Calendar date: {built.calendar_date}
- Page: `{built.filename}`
- Written with: {built.writer_note}
- Markers still to check: {built.markers} × `[FAISAL TO ADD]`

The pull request adds the page, a card at the top of the Insights list, a sitemap entry, and marks this calendar row as `drafted`.

Merge to publish. GitHub Pages updates a minute or two later. Close the pull request to discard it. Closing leaves the row as `queued` on main, so a later run can draft it again. To drop the topic instead, set its status to `skip` on main.

Read the post before merging. Do not merge it while a `[FAISAL TO ADD]` marker is still standing in for a figure, client, quote or result you have not checked. You can edit the file on the branch, then merge.

This is a normal pull request, so the Merge button is available. "Draft" in the title means the writing is for you to check.
"""


def build(
    root: Path,
    row_index: int,
    row: dict[str, str],
    key: str,
    force_placeholder: bool,
    call=None,
    wait=None,
) -> Built:
    reference_path = root / REFERENCE_POST
    if not reference_path.is_file():
        raise SystemExit(f"Missing the template file {REFERENCE_POST}. No draft was created.")
    blog_path = root / "blog.html"
    sitemap_path = root / "sitemap.xml"
    if not blog_path.is_file() or not sitemap_path.is_file():
        raise SystemExit("blog.html or sitemap.xml is missing. No draft was created.")
    parsed = date.fromisoformat(row["date"])
    slug = slugify(row["topic"])
    filename = unique_filename(root, slug)
    draft, writer, writer_note = write_draft(
        row["topic"], row["target_query"], row["notes"], key, force_placeholder, call=call, wait=wait
    )
    chrome = load_chrome(reference_path.read_text(encoding="utf-8"))
    related = choose_related(root, row["topic"], row["target_query"], row["notes"])
    page = render_page(
        chrome,
        filename,
        draft.title,
        draft.description,
        draft.excerpt,
        uk_date(parsed),
        parsed.isoformat(),
        draft.sections,
        related,
    )
    card = card_html(filename, uk_date(parsed), draft.title, draft.excerpt)
    return Built(
        filename=filename,
        slug=slug,
        title=draft.title,
        topic=row["topic"],
        query=row["target_query"],
        notes=row["notes"],
        calendar_date=parsed.isoformat(),
        writer=writer,
        writer_note=writer_note,
        html=page,
        card=card,
        markers=page.count("[FAISAL TO ADD]"),
        row_index=row_index,
    )


def apply_built(root: Path, built: Built, rows: list[dict[str, str]]) -> None:
    blog_path = root / "blog.html"
    sitemap_path = root / "sitemap.xml"
    calendar_path = root / CALENDAR_NAME
    new_blog = insert_card(blog_path.read_text(encoding="utf-8"), built.card)
    new_sitemap = insert_sitemap(sitemap_path.read_text(encoding="utf-8"), built.filename, built.calendar_date)
    rows[built.row_index]["status"] = "drafted"
    (root / built.filename).write_text(built.html, encoding="utf-8")
    blog_path.write_text(new_blog, encoding="utf-8")
    sitemap_path.write_text(new_sitemap, encoding="utf-8")
    write_calendar(calendar_path, rows)


def set_output(name: str, value: str) -> None:
    clean = collapse(value)
    print(f"{name}={clean}")
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"{name}={clean}\n")


def emit_built(built: Built, body_path: Path) -> None:
    body_path.write_text(pull_request_body(built), encoding="utf-8")
    set_output("created", "true")
    set_output("topic", built.topic)
    set_output("slug", built.slug)
    set_output("filename", built.filename)
    set_output("date", built.calendar_date)
    set_output("query", built.query)
    set_output("writer", built.writer)
    set_output("bodyfile", str(body_path))
    set_output("markers", str(built.markers))


def api_key(force_placeholder: bool) -> str:
    if force_placeholder:
        return ""
    return os.environ.get("GEMINI_API_KEY", "").strip()


def self_test() -> None:
    repo = Path(__file__).resolve().parents[1]
    reference = (repo / REFERENCE_POST).read_text(encoding="utf-8")
    assert MODEL_RULES in build_prompt("Topic", "query", "")
    assert "UK English" in MODEL_RULES
    assert "short sentences" in MODEL_RULES
    assert "No hype" in MODEL_RULES
    assert "No invented figures, clients, quotes or results" in MODEL_RULES
    assert "[FAISAL TO ADD]" in MODEL_RULES
    scrubbed = scrub_figures("Revenue grew 75% to £9 million in two weeks.")
    assert "75%" not in scrubbed and "£9" not in scrubbed
    assert "two weeks" in scrubbed
    assert "[FAISAL TO ADD]" in scrubbed
    assert slugify("How to hire a fractional marketing director") == "how-to-hire-a-fractional-marketing-director"
    assert slugify("IR35 & fees") == "ir35-fees"

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "repo"
        root.mkdir()
        shutil.copy(repo / REFERENCE_POST, root / REFERENCE_POST)
        shutil.copy(repo / "blog.html", root / "blog.html")
        shutil.copy(repo / "sitemap.xml", root / "sitemap.xml")
        for name in ("services.html", "case-studies.html"):
            shutil.copy(repo / name, root / name)
        before_blog = (root / "blog.html").read_text(encoding="utf-8")
        before_sitemap = (root / "sitemap.xml").read_text(encoding="utf-8")
        before_reference = (root / REFERENCE_POST).read_text(encoding="utf-8")
        cards_before = before_blog.count('class="blog-card"')
        (root / "content").mkdir()
        calendar = root / CALENDAR_NAME
        calendar.write_text(
            "date,topic,target_query,status,notes\n"
            '2026-12-08,"Later topic, with a comma",later query,queued,"keep; this note"\n'
            "2026-12-01,How a fractional marketing director spends the first month,fractional marketing director first month,queued,Dry-run sample\n"
            "2026-11-01,Already done,done,drafted,Leave this row\n",
            encoding="utf-8",
        )
        rows = read_calendar(calendar)
        picked = next_queued(rows)
        assert picked is not None
        index, row = picked
        assert row["topic"].startswith("How a fractional")
        built = build(root, index, row, key="", force_placeholder=True)
        assert built.writer == "placeholders"
        assert "[FAISAL TO ADD]" in built.html
        assert "How a fractional marketing director spends the first month" in built.html
        assert "fractional marketing director first month" in built.html
        assert 'href="style.css?v=' in built.html
        assert re.search(r'^<link rel="stylesheet" href="style\.css\?v=[^"]+"[^\n]*$', reference, re.M).group(0) in built.html
        assert re.search(r"<body>\n.*?</header>\n\n", reference, re.S).group(0) in built.html
        assert 'src="site.js?v=' in built.html
        assert "I use analytics" in built.html
        assert "Fractional CMO in Spain" in built.html
        assert f"{SITE}/#faisal" in built.html
        assert f"{SITE}/#org" in built.html
        assert f'href="{SITE}/{built.filename}"' in built.html or f'href="{SITE}/{built.filename}"' in built.html
        assert f'<link rel="canonical" href="{SITE}/{built.filename}">' in built.html
        assert 70 <= len(re.search(r'name="description" content="([^"]*)"', built.html).group(1)) <= 160

        sys.path.insert(0, str(repo / "scripts"))
        import site_health

        facts = site_health.analyse_html(built.html, f"{SITE}/{built.filename}")
        assert "BlogPosting" in facts.types
        assert facts.breadcrumb_ok, "Generated breadcrumb did not match Home > Insights > this page"
        assert facts.canonical == site_health.norm(f"{SITE}/{built.filename}")
        assert facts.title

        output = Path(tmp) / "preview"
        output.mkdir()
        snapshot = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
        # Dry-run writes only into the output directory.
        rows_copy = read_calendar(calendar)
        built_dry = build(root, index, rows_copy[index], key="", force_placeholder=True)
        (output / built_dry.filename).write_text(built_dry.html, encoding="utf-8")
        for path, content in snapshot.items():
            assert path.read_bytes() == content, f"Dry-run changed {path}"

        apply_built(root, built, rows)
        updated = read_calendar(calendar)
        assert updated[index]["status"] == "drafted"
        assert updated[index]["notes"] == "Dry-run sample"
        later = next(item for item in updated if item["topic"].startswith("Later topic"))
        assert later["status"] == "queued"
        assert later["topic"] == "Later topic, with a comma"
        assert later["notes"] == "keep; this note"
        assert (root / "blog.html").read_text(encoding="utf-8").count('class="blog-card"') == cards_before + 1
        assert INSIGHTS_MARKER + "\n" + built.card in (root / "blog.html").read_text(encoding="utf-8")
        sitemap = (root / "sitemap.xml").read_text(encoding="utf-8")
        assert f"{SITE}/{built.filename}" in sitemap
        import xml.etree.ElementTree as ET

        ET.fromstring(sitemap)
        assert (root / REFERENCE_POST).read_text(encoding="utf-8") == before_reference
        assert before_sitemap != sitemap
        assert before_blog != (root / "blog.html").read_text(encoding="utf-8")

        pr = "https://github.com/consultingfaisal/faisalconsulting.co.uk/pull/1"
        updated[index]["notes"] = add_pr_note(updated[index]["notes"], pr)
        write_calendar(calendar, updated)
        noted = read_calendar(calendar)[index]
        assert noted["notes"] == f"Dry-run sample; PR: {pr}"
        assert "GEMINI_API_KEY=" not in built.html
        assert "AIza" not in built.html

    _assert_gemini_retries()
    print("Draft post self-test passed.", file=sys.stderr)


def _sample_model_json() -> str:
    return json.dumps(
        {
            "title": "How a fractional marketing director spends the first month",
            "meta_description": (
                "What to line up in the first month with a fractional marketing director, "
                "before any new spend is agreed."
            ),
            "card_excerpt": "What to line up before any new spend is agreed.",
            "sections": [
                {"heading": "", "paragraphs": ["Start from the numbers you already trust."], "bullets": []},
                {"heading": "The first month", "paragraphs": ["Agree what a useful week looks like."], "bullets": []},
            ],
        }
    )


def _assert_gemini_retries() -> None:
    assert sum(RETRY_BACKOFF_SECONDS) == 600
    assert len(RETRY_BACKOFF_SECONDS) == MAX_MODEL_ATTEMPTS - 1
    topic = "How a fractional marketing director spends the first month"
    sleeps: list[float] = []

    def no_wait(seconds: float) -> None:
        sleeps.append(seconds)

    calls: list[tuple[str, bool]] = []

    def busy_then_ok(model: str, prompt: str, key: str, json_mode: bool) -> tuple[int, str]:
        calls.append((model, json_mode))
        if model == MODELS[0] and sum(1 for name, _mode in calls if name == MODELS[0]) >= 3:
            return 200, _sample_model_json()
        return 503, "high demand"

    sleeps.clear()
    calls.clear()
    draft, writer, _note = write_draft(topic, "query", "", "test-key", False, call=busy_then_ok, wait=no_wait)
    assert writer == "gemini"
    assert draft.title == topic
    assert sleeps == [30, 90]
    assert sum(1 for name, _mode in calls if name == MODELS[0]) == 3
    assert "[FAISAL TO ADD]" in MODEL_RULES

    calls.clear()
    sleeps.clear()

    def always_busy(model: str, prompt: str, key: str, json_mode: bool) -> tuple[int, str]:
        calls.append((model, json_mode))
        return 503, "high demand"

    try:
        write_draft(topic, "query", "", "test-key", False, call=always_busy, wait=no_wait)
    except GeminiUnavailable as exc:
        message = str(exc)
    else:
        raise SystemExit("Self-test failed. A run of HTTP 503 responses still returned a draft.")
    assert sleeps == [30, 90, 180, 300]
    assert "No pull request was opened" in message
    assert "still queued" in message
    assert all(sum(1 for name, _mode in calls if name == model) == MAX_MODEL_ATTEMPTS for model in MODELS)

    calls.clear()
    sleeps.clear()

    def missing(model: str, prompt: str, key: str, json_mode: bool) -> tuple[int, str]:
        calls.append((model, json_mode))
        return 404, "not found"

    try:
        write_draft(topic, "query", "", "test-key", False, call=missing, wait=no_wait)
    except GeminiUnavailable:
        pass
    else:
        raise SystemExit("Self-test failed. HTTP 404 still returned a draft.")
    assert sleeps == []
    assert len(calls) == len(MODELS)

    calls.clear()

    def limited_then_ok(model: str, prompt: str, key: str, json_mode: bool) -> tuple[int, str]:
        calls.append((model, json_mode))
        if model == MODELS[0] and sum(1 for name, _mode in calls if name == MODELS[0]) >= 2:
            return 200, _sample_model_json()
        if model == MODELS[0]:
            return 429, "slow down"
        return 503, "high demand"

    sleeps.clear()
    draft, writer, _note = write_draft(topic, "query", "", "test-key", False, call=limited_then_ok, wait=no_wait)
    assert writer == "gemini"
    assert sleeps == [30]
    assert sum(1 for name, _mode in calls if name == MODELS[0]) == 2

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        repo = Path(__file__).resolve().parents[1]
        shutil.copy(repo / REFERENCE_POST, root / REFERENCE_POST)
        shutil.copy(repo / "blog.html", root / "blog.html")
        shutil.copy(repo / "sitemap.xml", root / "sitemap.xml")
        (root / "content").mkdir()
        calendar = root / CALENDAR_NAME
        calendar.write_text(
            "date,topic,target_query,status,notes\n"
            "2026-11-10,Google Ads not converting,google ads not converting,queued,\n",
            encoding="utf-8",
        )
        before = calendar.read_text(encoding="utf-8")
        rows = read_calendar(calendar)
        picked = next_queued(rows)
        assert picked is not None
        try:
            build(root, picked[0], picked[1], "test-key", False, call=always_busy, wait=no_wait)
        except GeminiUnavailable as exc:
            assert "No pull request was opened" in str(exc)
        else:
            raise SystemExit("Self-test failed. build() opened a draft after Gemini kept returning HTTP 503.")
        assert calendar.read_text(encoding="utf-8") == before
        assert not (root / "blog-google-ads-not-converting.html").exists()
        assert picked[1]["status"] == "queued"


def main() -> int:
    parser = argparse.ArgumentParser(description="Draft the next queued calendar row as a blog post.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--calendar", default="")
    parser.add_argument("--apply", action="store_true", help="Write the draft into the repository. Does not commit.")
    parser.add_argument("--dry-run", action="store_true", help="Write a preview outside the repository.")
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--set-pr-url", default="", help="Record a pull request URL on a drafted calendar row.")
    parser.add_argument("--date", default="")
    parser.add_argument("--topic", default="")
    parser.add_argument("--force-placeholder", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0

    root = Path(args.root).resolve()
    calendar_path = Path(args.calendar) if args.calendar else root / CALENDAR_NAME
    if args.set_pr_url:
        if not args.date or not args.topic:
            raise SystemExit("--set-pr-url needs --date and --topic.")
        rows = read_calendar(calendar_path)
        matched = False
        for row in rows:
            if row["date"] == args.date and row["topic"] == args.topic:
                row["status"] = "drafted"
                row["notes"] = add_pr_note(row["notes"], args.set_pr_url)
                matched = True
                break
        if not matched:
            raise SystemExit("Could not find that calendar row to add the pull request link.")
        write_calendar(calendar_path, rows)
        log(f"Recorded {args.set_pr_url}")
        return 0

    if not args.apply and not args.dry_run:
        parser.error("Choose --apply, --dry-run, --set-pr-url, or --self-test.")

    rows = read_calendar(calendar_path)
    picked = next_queued(rows)
    if picked is None:
        set_output("created", "false")
        log("No queued topics in the content calendar. Nothing was drafted.")
        return 0
    index, row = picked
    try:
        built = build(root, index, row, api_key(args.force_placeholder), args.force_placeholder)
    except GeminiUnavailable as exc:
        set_output("created", "false")
        log(str(exc))
        return 1
    if args.dry_run:
        output = Path(args.output_dir) if args.output_dir else Path(tempfile.mkdtemp(prefix="draft-preview-"))
        output.mkdir(parents=True, exist_ok=True)
        (output / built.filename).write_text(built.html, encoding="utf-8")
        blog = insert_card((root / "blog.html").read_text(encoding="utf-8"), built.card)
        sitemap = insert_sitemap((root / "sitemap.xml").read_text(encoding="utf-8"), built.filename, built.calendar_date)
        (output / "blog.html").write_text(blog, encoding="utf-8")
        (output / "sitemap.xml").write_text(sitemap, encoding="utf-8")
        preview_rows = [dict(item) for item in rows]
        preview_rows[index]["status"] = "drafted"
        write_calendar(output / "calendar.csv", preview_rows)
        body = output / "pr-body.md"
        body.write_text(pull_request_body(built), encoding="utf-8")
        log(f"Dry run only. Preview written to {output}")
        log(f"Page: {built.filename}")
        log(f"Writer: {built.writer_note}")
        log(f"Markers: {built.markers}")
        emit_built(built, body)
        return 0

    apply_built(root, built, rows)
    body_dir = Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir()))
    emit_built(built, body_dir / "draft-pr-body.md")
    log(f"Drafted {built.filename}. {built.writer_note}")
    log("Files changed in the working tree. The workflow commits them on a draft branch, not on main.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(0)
