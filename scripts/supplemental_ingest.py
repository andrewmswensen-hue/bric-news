#!/usr/bin/env python3
"""
BRIC.News supplemental ingest.

Reads supplemental_sources.yaml, fetches RSS + Google News items,
dedupes against local state and the Monday pipeline's item-registry.json,
pulls article bodies with readability-lxml, and sends the result through
a two-stage Anthropic pipeline (Haiku for scoring, Opus for rewrite).

Output: /scripts/queue/YYYY-MM-DD-slug.json for human review.
Never writes directly into /src/content/items/ — that's the review tool's job.

Voice constraints are enforced in the system prompt: neutral newswire-voice
summary (<=100 words), educational single-sentence "why it matters" line,
no em dashes, no AI buzzwords, no RLPM mentions.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.robotparser
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import feedparser
import httpx
import yaml
from readability import Document as ReadabilityDocument
from slugify import slugify

try:
    from anthropic import Anthropic
except ImportError:
    Anthropic = None  # allows --dry-run without the SDK installed

# -----------------------------------------------------------------------------
# Paths and constants
# -----------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
QUEUE_DIR = SCRIPT_DIR / "queue"
ITEMS_DIR = REPO_ROOT / "src" / "content" / "items"
STATE_PATH = SCRIPT_DIR / "state.json"
SOURCES_PATH = SCRIPT_DIR / "supplemental_sources.yaml"
ITEM_REGISTRY_PATH = Path(os.environ.get("ITEM_REGISTRY_PATH", SCRIPT_DIR / "item-registry.json"))
if not ITEM_REGISTRY_PATH.is_absolute():
    ITEM_REGISTRY_PATH = REPO_ROOT / ITEM_REGISTRY_PATH

MODEL_SCORER = "claude-haiku-4-5-20251001"
MODEL_WRITER = "claude-opus-4-7"

# Paywall heuristics: if the response body is short AND domain is in this list
PAYWALL_DOMAINS = {
    "wsj.com", "ft.com", "nytimes.com", "bostonglobe.com",
    "bloomberg.com", "thetimes.com", "economist.com",
    "bizjournals.com",  # often partial paywalls
}
PAYWALL_BODY_CHAR_THRESHOLD = 500

USER_AGENT = "BRIC.News ingest bot/1.0 (+https://bric.news/about)"
HTTP_TIMEOUT = 20.0

MUNICIPALITIES = {
    "columbus", "dublin", "westerville", "grove-city", "gahanna", "hilliard",
    "reynoldsburg", "worthington", "upper-arlington", "bexley",
    "franklin-county", "delaware-county", "licking-county",
}
CONTENT_TYPES = {"policy", "news", "market_data", "event"}

SYSTEM_PROMPT_WRITER = """You are an editorial assistant for BRIC.News, a Columbus, Ohio investor news publication. Write in two voices:

1. SUMMARY (<=100 words): neutral, factual, newswire-style. What happened, who, where, when. Columbus-first framing when the source allows. No editorializing adjectives, no opinion, no sides.

2. WHY IT MATTERS (one sentence, max two): educational and supportive. Voice of a knowledgeable Columbus investor friend helpfully explaining why this item shows up today. Value-driven. Never promotional. Never urgent. Never a CTA.

Never use em dashes. Use parentheses, en dashes, commas, or colons instead.
Never use AI buzzwords (game-changer, supercharge, unlock, crush, dominate, gold mine).
Never use absolutes (always, never, perfect, guaranteed).
Never give legal advice.
Never mention RL Property Management or any property management company by name unless they are the subject of the news.

Respond with ONLY a single JSON object matching the schema requested by the user. No prose outside the JSON."""

SYSTEM_PROMPT_SCORER = """You are a relevance scorer for BRIC.News, a Columbus-metro investor news publication.

Score each item 1-10 based on how useful it is for Columbus-area real estate investors and landlords.
- 9-10: directly affects Columbus investor decisions (policy, local law, tax changes, major local market data)
- 7-8: Columbus-area development, market reports, Columbus-specific events
- 5-6: Ohio-wide or regional items with Columbus relevance
- 1-4: tangential, national-only, or low signal

Respond with ONLY a JSON object: {"relevance_score": N}"""


# -----------------------------------------------------------------------------
# Utilities
# -----------------------------------------------------------------------------

def log(msg: str) -> None:
    print(msg, flush=True)


def normalize_url(url: str) -> str:
    """Strip tracking params and fragments. Lowercase scheme/host."""
    try:
        parsed = urllib.parse.urlsplit(url)
    except Exception:
        return url
    dropped_params = {
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        "fbclid", "gclid", "mc_cid", "mc_eid", "ref",
    }
    query = [
        (k, v) for k, v in urllib.parse.parse_qsl(parsed.query)
        if k.lower() not in dropped_params
    ]
    return urllib.parse.urlunsplit((
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        parsed.path,
        urllib.parse.urlencode(query),
        "",
    ))


def make_fingerprint(title: str, municipalities: list[str]) -> str:
    """Same format as Monday skill: lowercase, stripped punctuation, title + first municipality."""
    title_norm = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
    title_norm = re.sub(r"[^\w\s]", " ", title_norm).lower()
    title_norm = re.sub(r"\s+", " ", title_norm).strip()
    muni = (municipalities[0] if municipalities else "").lower()
    return f"{title_norm}|{muni}"


def safe_domain(url: str) -> str:
    try:
        return urllib.parse.urlsplit(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except json.JSONDecodeError:
            pass
    return {"seen_urls": [], "seen_fingerprints": []}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2))


def load_existing_fingerprints() -> set[str]:
    """Fingerprints from existing items/*.md frontmatter."""
    fps: set[str] = set()
    if not ITEMS_DIR.exists():
        return fps
    for md in ITEMS_DIR.glob("*.md"):
        text = md.read_text(errors="ignore")
        m = re.search(r"^fingerprint:\s*\"?([^\"\n]+)\"?\s*$", text, re.MULTILINE)
        if m:
            fps.add(m.group(1).strip())
    return fps


def load_registry_fingerprints() -> set[str]:
    """Fingerprints from Monday pipeline's item-registry.json, if present."""
    if not ITEM_REGISTRY_PATH.exists():
        return set()
    try:
        data = json.loads(ITEM_REGISTRY_PATH.read_text())
    except json.JSONDecodeError:
        return set()
    # Registry may be either {fp: {...}} or {"items": {fp: {...}}}; accept both
    items = data.get("items", data) if isinstance(data, dict) else {}
    if isinstance(items, dict):
        return {str(k) for k in items.keys()}
    return set()


def robots_allowed(url: str, session_parsers: dict[str, urllib.robotparser.RobotFileParser]) -> bool:
    parsed = urllib.parse.urlsplit(url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    if root not in session_parsers:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(f"{root}/robots.txt")
        try:
            rp.read()
        except Exception:
            # If robots.txt unreachable, assume allowed
            rp = None  # type: ignore
        session_parsers[root] = rp  # type: ignore
    rp = session_parsers[root]
    if rp is None:
        return True
    try:
        return rp.can_fetch(USER_AGENT, url)
    except Exception:
        return True


# -----------------------------------------------------------------------------
# Source fetching
# -----------------------------------------------------------------------------

@dataclass
class RawItem:
    title: str
    url: str
    published_at: datetime
    source_category_hint: str
    source_label: str
    source_domain: str = ""
    body: str = ""
    fingerprint: str = ""


def iter_rss(url: str) -> list[dict]:
    feed = feedparser.parse(url, agent=USER_AGENT)
    return feed.entries or []


def iter_google_news(query: str) -> list[dict]:
    q = urllib.parse.quote_plus(query)
    rss_url = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
    feed = feedparser.parse(rss_url, agent=USER_AGENT)
    return feed.entries or []


def parse_published(entry: dict) -> datetime:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            try:
                return datetime(*t[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    return datetime.now(timezone.utc)


def collect_raw_items(sources: list[dict]) -> list[RawItem]:
    items: list[RawItem] = []
    for src in sources:
        stype = src.get("type")
        cat = src.get("category", "news")
        label = src.get("label", "")
        entries: list[dict] = []
        try:
            if stype == "rss_feed":
                entries = iter_rss(src["url"])
            elif stype == "google_news_query":
                entries = iter_google_news(src["query"])
            else:
                log(f"[warn] Unknown source type: {stype}")
                continue
        except Exception as e:
            log(f"[err] source '{label}' failed: {e}")
            continue

        for entry in entries:
            title = (entry.get("title") or "").strip()
            url = entry.get("link") or entry.get("id") or ""
            if not title or not url:
                continue
            items.append(RawItem(
                title=title,
                url=normalize_url(url),
                published_at=parse_published(entry),
                source_category_hint=cat,
                source_label=label,
            ))
    return items


# -----------------------------------------------------------------------------
# HTTP fetch + body extraction
# -----------------------------------------------------------------------------

def fetch_body(client: httpx.Client, url: str, retries: int = 3) -> tuple[str, str]:
    """Return (body_text, final_url) or ('', '') if unfetchable."""
    backoff = 1.5
    for attempt in range(retries):
        try:
            r = client.get(url, follow_redirects=True, timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT})
            if r.status_code in (429, 503):
                time.sleep(backoff * (attempt + 1) + random.random())
                continue
            r.raise_for_status()
            doc = ReadabilityDocument(r.text)
            html = doc.summary(html_partial=True)
            text = re.sub(r"<[^>]+>", " ", html)
            text = re.sub(r"\s+", " ", text).strip()
            return text, str(r.url)
        except Exception:
            if attempt == retries - 1:
                return "", url
            time.sleep(backoff * (attempt + 1) + random.random())
    return "", url


def is_paywalled(body: str, domain: str) -> bool:
    short = len(body) < PAYWALL_BODY_CHAR_THRESHOLD
    return short and any(domain.endswith(d) for d in PAYWALL_DOMAINS)


# -----------------------------------------------------------------------------
# Anthropic calls
# -----------------------------------------------------------------------------

def score_relevance(client: Any, title: str, body_snippet: str) -> int:
    msg = client.messages.create(
        model=MODEL_SCORER,
        max_tokens=100,
        system=SYSTEM_PROMPT_SCORER,
        messages=[{
            "role": "user",
            "content": f"TITLE: {title}\n\nBODY (first 1000 chars):\n{body_snippet[:1000]}",
        }],
    )
    text = "".join(block.text for block in msg.content if getattr(block, "type", None) == "text")
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return 5
    try:
        return int(json.loads(match.group(0)).get("relevance_score", 5))
    except Exception:
        return 5


def rewrite_item(client: Any, raw: RawItem, body: str) -> dict | None:
    user_msg = (
        f"Rewrite this into the BRIC.News two-voice format.\n\n"
        f"TITLE: {raw.title}\n"
        f"SOURCE URL: {raw.url}\n"
        f"PUBLISHED: {raw.published_at.isoformat()}\n"
        f"SOURCE CATEGORY HINT: {raw.source_category_hint}\n\n"
        f"ARTICLE BODY:\n{body[:8000]}\n\n"
        f"Return a JSON object with this exact schema:\n"
        "{\n"
        '  "summary": "<=100 words neutral newswire summary",\n'
        '  "why_it_matters": "single educational sentence",\n'
        f'  "topics": ["subset of: policy, tax, market, legal, development, event, vendor, utilities, enforcement, incentives"],\n'
        f'  "municipalities": ["subset of: {", ".join(sorted(MUNICIPALITIES))}"],\n'
        '  "entities": ["org or person names mentioned"],\n'
        f'  "content_type": "one of: {", ".join(sorted(CONTENT_TYPES))}",\n'
        '  "risk_flags": ["any ambiguity notes for human review"]\n'
        "}"
    )
    msg = client.messages.create(
        model=MODEL_WRITER,
        max_tokens=1500,
        system=SYSTEM_PROMPT_WRITER,
        messages=[{"role": "user", "content": user_msg}],
    )
    text = "".join(block.text for block in msg.content if getattr(block, "type", None) == "text")
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None

    # Normalize / validate
    data.setdefault("topics", [])
    data.setdefault("municipalities", [])
    data.setdefault("entities", [])
    data.setdefault("risk_flags", [])
    data["topics"] = [t for t in data.get("topics", []) if isinstance(t, str)]
    data["municipalities"] = [
        m for m in data.get("municipalities", [])
        if isinstance(m, str) and m in MUNICIPALITIES
    ]
    ct = data.get("content_type", raw.source_category_hint)
    if ct not in CONTENT_TYPES:
        ct = raw.source_category_hint if raw.source_category_hint in CONTENT_TYPES else "news"
    data["content_type"] = ct
    return data


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="BRIC.News supplemental ingest")
    parser.add_argument("--dry-run", action="store_true", help="Skip Anthropic calls and queue writes")
    parser.add_argument("--limit", type=int, default=25, help="Max items to process per run")
    args = parser.parse_args()

    if not SOURCES_PATH.exists():
        log(f"[err] missing {SOURCES_PATH}")
        return 1

    with SOURCES_PATH.open() as f:
        cfg = yaml.safe_load(f) or {}
    sources = cfg.get("sources", [])
    if not sources:
        log("[warn] no sources configured")
        return 0

    state = load_state()
    seen_urls = set(state.get("seen_urls", []))
    seen_fps = set(state.get("seen_fingerprints", []))
    seen_fps |= load_existing_fingerprints()
    seen_fps |= load_registry_fingerprints()

    log(f"[info] loaded {len(sources)} sources, {len(seen_urls)} seen urls, {len(seen_fps)} known fingerprints")

    raw = collect_raw_items(sources)
    log(f"[info] collected {len(raw)} raw items from feeds")

    # Dedupe by URL first (cheap)
    unique: list[RawItem] = []
    for item in raw:
        if item.url in seen_urls:
            continue
        seen_urls.add(item.url)
        unique.append(item)

    n_new = 0
    n_dupes = len(raw) - len(unique)
    n_paywall = 0
    n_low_relevance = 0
    n_errors = 0
    n_robots = 0

    if args.dry_run:
        log(f"[dry-run] would fetch {min(len(unique), args.limit)} unique items")
        # Still validate dedup logic and fingerprint generation
        for item in unique[:args.limit]:
            item.fingerprint = make_fingerprint(item.title, [])
            if item.fingerprint in seen_fps:
                n_dupes += 1
        log(f"[dry-run] summary: {len(unique)} new-url, {n_dupes} dupe-fp")
        return 0

    client = None
    if Anthropic is None or not os.environ.get("ANTHROPIC_API_KEY"):
        log("[err] ANTHROPIC_API_KEY missing or anthropic SDK not installed — exiting (use --dry-run to test without)")
        return 2
    client = Anthropic()

    robots_cache: dict[str, urllib.robotparser.RobotFileParser] = {}
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)

    with httpx.Client() as http:
        for item in unique[:args.limit]:
            domain = safe_domain(item.url)
            item.source_domain = domain

            if not robots_allowed(item.url, robots_cache):
                log(f"[robots] {item.url}")
                n_robots += 1
                continue

            body, final_url = fetch_body(http, item.url)
            if not body:
                n_errors += 1
                continue

            if is_paywalled(body, domain):
                log(f"[paywall] {item.url}")
                n_paywall += 1
                continue

            # Stage 1: relevance score with Haiku
            try:
                score = score_relevance(client, item.title, body)
            except Exception as e:
                log(f"[err] score failed for {item.url}: {e}")
                n_errors += 1
                continue

            if score < 5:
                n_low_relevance += 1
                continue

            # Stage 2: full rewrite with Opus
            try:
                rewrite = rewrite_item(client, item, body)
            except Exception as e:
                log(f"[err] rewrite failed for {item.url}: {e}")
                n_errors += 1
                continue
            if not rewrite:
                n_errors += 1
                continue

            fp = make_fingerprint(item.title, rewrite.get("municipalities", []))
            if fp in seen_fps:
                n_dupes += 1
                continue
            seen_fps.add(fp)

            record = {
                "title": item.title,
                "summary": rewrite["summary"],
                "why_it_matters": rewrite["why_it_matters"],
                "source_url": item.url,
                "source_domain": domain,
                "published_at": item.published_at.isoformat(),
                "topics": rewrite.get("topics", []),
                "municipalities": rewrite.get("municipalities", []),
                "content_type": rewrite["content_type"],
                "entities": rewrite.get("entities", []),
                "fingerprint": fp,
                "risk_flags": rewrite.get("risk_flags", []),
                "relevance_score": score,
                "featured": False,
            }

            date_part = item.published_at.strftime("%Y-%m-%d")
            slug = slugify(item.title)[:60] or "untitled"
            filename = f"{date_part}-{slug}.json"
            out_path = QUEUE_DIR / filename
            counter = 1
            while out_path.exists():
                out_path = QUEUE_DIR / f"{date_part}-{slug}-{counter}.json"
                counter += 1
            out_path.write_text(json.dumps(record, indent=2))
            n_new += 1
            log(f"[queued] {out_path.name} (score={score})")

    state["seen_urls"] = sorted(seen_urls)[-5000:]  # cap growth
    state["seen_fingerprints"] = sorted(seen_fps)[-5000:]
    save_state(state)

    log(
        f"[done] {n_new} new, {n_dupes} dupes, {n_paywall} paywall-skipped, "
        f"{n_low_relevance} low-relevance, {n_robots} robots-blocked, {n_errors} errors"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
