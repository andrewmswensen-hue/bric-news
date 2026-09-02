#!/usr/bin/env python3
"""
BRIC.News syndication ingest (v2, Sep 2026).

Reads scripts/supplemental_sources.yaml, pulls every RSS feed and Google News
query, and turns the day's most relevant items into markdown files in
src/content/items/. Runs daily from GitHub Actions; can also run by hand.

The pipeline, in order:

  1. COLLECT   Fetch every feed. Drop entries that are too old, come from a
               blocklisted domain, match a kill pattern in the title, or were
               seen on a previous run. Google News links are resolved to the
               original publisher's URL before anything else happens.
  2. SCORE     Haiku scores each candidate against its tier's rubric using the
               title and feed snippet only. No article fetch yet, so this is
               cheap and light on publishers' servers.
  3. SELECT    Per tier: keep items at or above the tier's bar, rank by score,
               and take only as many as the tier's daily cap allows.
  4. WRITE     For each selected item: honor robots.txt, fetch the article,
               skip paywalls and thin pages, have Sonnet write the BRIC summary
               / detail / why-it-matters, reject generic why-it-matters lines,
               dedupe by fingerprint, and write the markdown file. If an item
               fails at this stage the next-best one from that tier takes its
               slot, so caps fill with real content.

Legal posture is enforced in code, not left to prompts: robots.txt is honored,
paywalls are skipped rather than bypassed, blocklisted publishers are never
fetched, every item carries a source_url and source_name, and summaries are
rewritten from facts rather than copied.

Usage:
  python scripts/supplemental_ingest.py --dry-run          # no API calls, no writes
  python scripts/supplemental_ingest.py --limit 5          # small real run
  python scripts/supplemental_ingest.py --source rlpm-blog # one source only
  python scripts/supplemental_ingest.py --tier local       # one tier only
"""

from __future__ import annotations

import argparse
import html
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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import feedparser
import httpx
import yaml
from readability import Document as ReadabilityDocument
from slugify import slugify

try:
    from anthropic import Anthropic, APIConnectionError, APIStatusError, RateLimitError
except ImportError:  # allows --dry-run without the SDK installed
    Anthropic = None  # type: ignore
    APIConnectionError = APIStatusError = RateLimitError = Exception  # type: ignore

try:
    from googlenewsdecoder import gnewsdecoder
except ImportError:
    gnewsdecoder = None  # type: ignore

# -----------------------------------------------------------------------------
# Paths and constants
# -----------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
ITEMS_DIR = REPO_ROOT / "src" / "content" / "items"
STATE_PATH = SCRIPT_DIR / "state.json"
SOURCES_PATH = SCRIPT_DIR / "supplemental_sources.yaml"
LAST_RUN_JSON = SCRIPT_DIR / "last_run.json"
LAST_RUN_MD = SCRIPT_DIR / "last_run.md"
ITEM_REGISTRY_PATH = Path(os.environ.get("ITEM_REGISTRY_PATH", SCRIPT_DIR / "item-registry.json"))
if not ITEM_REGISTRY_PATH.is_absolute():
    ITEM_REGISTRY_PATH = REPO_ROOT / ITEM_REGISTRY_PATH

# Cheap model scores, capable model writes. Both chosen by the publisher for
# cost: this is summarization, not analysis.
MODEL_SCORER = "claude-haiku-4-5"
MODEL_WRITER = "claude-sonnet-5"
PROMPT_VERSION = "2026-09-01.v2"

# Approximate list prices per million tokens, used only for the cost line in
# the run summary. Update if pricing changes.
PRICE_PER_M = {
    MODEL_SCORER: (1.00, 5.00),
    MODEL_WRITER: (2.00, 10.00),
}

USER_AGENT = "BRIC.News ingest bot/2.0 (+https://bric.news/about)"
BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
HTTP_TIMEOUT = 20.0
PAYWALL_BODY_CHAR_THRESHOLD = 500

MUNICIPALITIES = [
    "columbus", "dublin", "westerville", "grove-city", "gahanna", "hilliard",
    "reynoldsburg", "worthington", "upper-arlington", "bexley",
    "franklin-county", "delaware-county", "licking-county",
]
TOPICS = [
    "policy", "tax", "market", "legal", "development", "event",
    "vendor", "utilities", "enforcement", "incentives",
]
CONTENT_TYPES = ["policy", "news", "market_data", "event"]
SCOPES = ["local", "regional", "state", "national"]

# Domains that map to a partner. Content from these is rewritten with
# permission and labeled as partner content on the site.
PARTNER_DOMAINS = {"rlpmg.com": "rlpm"}

# Titles matching any of these are dropped before scoring. Cheap, obvious kills.
TITLE_KILL_PATTERNS = [
    re.compile(r"^\s*\d+\s+(best|top|ways|things|reasons|tips|signs|mistakes)\b", re.I),
    re.compile(r"\b(sponsored|paid post|advertorial|partner content)\b", re.I),
    re.compile(r"^\s*(opinion|editorial|op-ed|column|letter)\s*[:|\-–]", re.I),
    re.compile(r"\bpress release\b", re.I),
    re.compile(r"\b(podcast|episode\s+\d+|webinar|livestream)\b", re.I),
    re.compile(r"\b(giveaway|quiz|horoscope|crossword|obituar)", re.I),
    re.compile(r"\b(celebrity|mansion|penthouse)\b", re.I),
    re.compile(r"\b(news roundup|weekly roundup|catching our eye|in case you missed)\b", re.I),
    # Daily rate tickers. Monthly recaps survive because they do not match these shapes.
    re.compile(r"\b(today'?s|current|daily)\b.*\bmortgage rates?\b", re.I),
    re.compile(r"\bmortgage rates?\b.*\b(today|for (jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\.? \d{1,2})\b", re.I),
    re.compile(r"\bcompare\b.*\bmortgage rates\b", re.I),
]

# Google News search occasionally surfaces non-US outlets on generic terms.
# Nothing published under these country domains can be Columbus-relevant.
FOREIGN_TLDS = (".ua", ".in", ".de", ".uk", ".co.uk", ".au", ".ca", ".ie", ".nz", ".za", ".ph", ".ng", ".pk", ".fr", ".es", ".it", ".nl")

# A why-it-matters line that says nothing. If the model produces one of these
# shapes the item is rejected regardless of score.
GENERIC_WHY_PATTERNS = [
    re.compile(r"\b(stay|keep) (informed|an eye|up to date|aware)\b", re.I),
    re.compile(r"\bworth (watching|noting|keeping an eye on)\b", re.I),
    re.compile(r"\b(could|may|might) (affect|impact) (investors|landlords)\b", re.I),
    re.compile(r"\bimportant (for|to) (investors|landlords) to know\b", re.I),
]

# -----------------------------------------------------------------------------
# Prompts
# -----------------------------------------------------------------------------

TIER_RUBRICS = {
    "national": """NATIONAL tier. The bar is high: a national story earns a slot on a Columbus site only if it changes what a Columbus-area landlord, property manager, or small investor actually does, budgets, or complies with.

THE COLUMBUS READ-THROUGH TEST, which every national item must pass before any score above 6: can you name the specific thing a Columbus landlord does differently, pays differently, or must comply with because of this story? Not "it is about housing" and not "investors care about rates." A concrete consequence for an owner of a few rental units in Franklin, Delaware, or Licking County. If you cannot name one in a sentence, the item scores 6 or below no matter how important the story is nationally. Being genuinely major news is not the same as being usable news for this audience.

Score 7-10 only for:
- Federal rules reaching rental operations: HUD, FHFA, FHA, Section 8 / housing choice voucher program changes
- Tax law touching depreciation, 1031 exchanges, mortgage interest, or pass-through treatment
- Tenant screening and credit reporting regulation (FCRA, background checks)
- Fair housing enforcement actions and precedent-setting rulings
- Insurance market shifts that change what landlords pay or can get covered
- MONTHLY mortgage rate recaps and lending policy shifts that explain a consequence
- Genuinely national operating shifts: institutional buying, build-to-rent, major software or utility changes

Score 1-5 for: daily mortgage rate ticks, national home-sale stats with no Ohio or Columbus read-through, luxury or coastal real estate, general macroeconomics, listicles and rankings, evergreen how-to content, opinion and prediction pieces, anything a state or local outlet covers better.""",

    "state": """STATE tier (Ohio). A statewide story needs a clear housing or property nexus and a plausible effect on Columbus-area landlords and investors.

Score 7-10 for:
- Ohio bills affecting landlord-tenant law, especially anything touching ORC Chapter 5321
- Property tax law, valuation rules, and the reappraisal cycle
- State budget items funding or restricting housing
- Ohio Supreme Court and appellate rulings on eviction, habitability, zoning authority, or property tax
- State preemption fights over local rental registration or rent regulation
- Licensing, contractor, and inspection rule changes
- Statewide programs, grants, and housing trust fund activity
- WEEKLY or MONTHLY statewide home sales and price reports

Score 1-5 for: Ohio politics with no housing nexus, other-city local stories (Cleveland, Cincinnati, Toledo) unless precedent-setting, economic development with no residential angle, bills at introduction with no cosponsors and no hearing.""",

    "regional": """REGIONAL tier (Central Ohio beyond the core 13 jurisdictions). Score 6-10 for property, zoning, tax, development, or landlord-tenant items in the wider Columbus metro that a Columbus investor would reasonably act on. Score 1-5 for anything without a property angle.""",

    "local": """LOCAL tier (Columbus metro: Franklin, Delaware, Licking counties and the cities in them). The bar is lower because local is the point of the site, but it still has to be about property, money, or rules.

Score 5-10 for:
- Municipal zoning, code, rental registration, and inspection changes
- County auditor and treasurer actions, especially reappraisal and property tax
- Development, rezoning, and significant permit activity
- Local court and eviction practice changes
- Utility rate changes and infrastructure assessments
- WEEKLY or MONTHLY Columbus-metro home sales, price, and rent reports
- REIA, BIA, and other investor events worth attending
- Educational explainers on Ohio landlord operations (leases, deposits, maintenance, tenant law)
- New public resources: lookup tools, forms, portals, fee schedules
- Housing service and legal aid organizations (for example CRIS Ohio) ONLY when the item reaches a landlord's own decisions: housing voucher and Section 8 access or program changes, rulings on work authorization or immigration status that affect income verification and tenant screening, eviction or fair housing legal action, or a shift in resettlement volume large enough to move rental demand. Their staff spotlights, fundraising, donor news, volunteer drives, and program celebrations are not news for this audience: score those 1-3.
- Semi-real-estate stories that still carry an investor angle: a major employer expansion that will move rents, a transit or road project that changes a neighborhood, a school or infrastructure decision that affects property values. Nuance is allowed here when the investor angle is real.

Score 1-4 for: crime blotter, general civic news, restaurant and retail openings with no real estate angle, individual home sales and single listings, sports, vendor press releases dressed as news, promotion of a specific property management company other than an educational explainer.""",
}

SYSTEM_PROMPT_SCORER = """You are the relevance scorer for BRIC.News, a Columbus, Ohio publication for real estate investors, landlords, and property managers. You see only a headline and a short feed snippet. Score conservatively: when in doubt, score low. A wrong "include" costs the publisher money and reader trust; a wrong "exclude" costs nothing.

Set kill=true (and give a short kill_reason) when the item is:
- an opinion piece, prediction piece, listicle, ranking, quiz, or evergreen how-to with no news hook (partner content is the exception: educational explainers from a partner are allowed)
- marketing copy, a press release, or promotion of a specific company or product
- about a named private individual's property or finances
- unrelated to housing, property, land use, taxes, or landlord operations
- a daily mortgage-rate tick or other daily market noise (monthly recaps are fine)

Respond with only the JSON object."""

SYSTEM_PROMPT_WRITER = """You are the editorial assistant for BRIC.News, a Columbus, Ohio publication for real estate investors, landlords, and property managers. You rewrite a source article into BRIC's house format. BRIC is a pointer, not a republisher: every item credits the publisher and links to the original, so your job is an original, factual account written from the facts, never the publisher's sentences.

Produce:

title: A BRIC headline, at most 90 characters, factual, specific, present tense where natural. Do not reuse the publisher's headline verbatim.

summary: At most 100 words. Neutral newswire voice. What happened, who, where, when, with the specific numbers from the source. Columbus-first framing when the source allows. No opinion, no adjectives that editorialize.

detail: 150 to 250 words (250 to 350 for partner content). The fuller account a reader sees when they open the item. Cover the key facts, the mechanism, the timeline, and who is affected. Attribute contested claims to the source in the text ("according to the Ohio Capital Journal"). Do not quote more than a short phrase. Written from the facts in your own words.

why_it_matters: One or two sentences, educational and specific to Columbus-area investors and landlords. The voice of a knowledgeable local investor friend explaining why this item shows up today. Name the concrete consequence, the number, the deadline, or the decision it affects. Never generic. Never a call to action. Never urgent.

why_specificity: Your honest 1-5 rating of how specific and non-generic the why_it_matters line is. 1 = could apply to any story. 5 = names a concrete consequence unique to this story.

Also classify the item (topics, municipalities when local, content_type, entities) and give the publisher's name as source_name.

Rules that apply to every field:
- Never use em dashes. Use commas, colons, parentheses, or en dashes.
- No AI buzzwords (game-changer, unlock, supercharge, crush, dominate, gold mine, dive into, landscape).
- No absolutes (always, never, perfect, guaranteed).
- No legal advice and no investment advice. Report rules; do not tell readers what to do.
- Do not mention any property management company by name unless it is the subject of the news.
- If the source does not support a fact, leave it out. Do not fill gaps.

Respond with only the JSON object."""

SCORER_SCHEMA = {
    "type": "object",
    "properties": {
        "relevance_score": {"type": "integer", "minimum": 1, "maximum": 10},
        "kill": {"type": "boolean"},
        "kill_reason": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["relevance_score", "kill", "kill_reason", "reason"],
    "additionalProperties": False,
}

WRITER_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "detail": {"type": "string"},
        "why_it_matters": {"type": "string"},
        "why_specificity": {"type": "integer", "minimum": 1, "maximum": 5},
        "topics": {"type": "array", "items": {"type": "string", "enum": TOPICS}},
        "municipalities": {"type": "array", "items": {"type": "string", "enum": MUNICIPALITIES}},
        "content_type": {"type": "string", "enum": CONTENT_TYPES},
        "entities": {"type": "array", "items": {"type": "string"}},
        "source_name": {"type": "string"},
        "risk_flags": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "title", "summary", "detail", "why_it_matters", "why_specificity",
        "topics", "municipalities", "content_type", "entities", "source_name", "risk_flags",
    ],
    "additionalProperties": False,
}

# -----------------------------------------------------------------------------
# Config and state
# -----------------------------------------------------------------------------

@dataclass
class Settings:
    max_age_days: int = 3
    max_items_per_source: int = 10
    min_body_chars: int = 400
    review_required_until: date | None = None
    front_page_max: int = 18


@dataclass
class Tier:
    name: str
    min_score: int
    daily_cap: int


@dataclass
class Source:
    id: str
    label: str
    type: str
    scope: str
    category: str
    url: str = ""
    query: str = ""
    partner: str | None = None
    min_score: int | None = None


@dataclass
class Config:
    settings: Settings
    tiers: dict[str, Tier]
    sources: list[Source]
    blocklist: list[str]


def load_config() -> Config:
    with SOURCES_PATH.open() as f:
        raw = yaml.safe_load(f) or {}
    s = raw.get("settings", {}) or {}
    rru = s.get("review_required_until")
    if isinstance(rru, str):
        rru = date.fromisoformat(rru)
    settings = Settings(
        max_age_days=int(s.get("max_age_days", 3)),
        max_items_per_source=int(s.get("max_items_per_source", 10)),
        min_body_chars=int(s.get("min_body_chars", 400)),
        review_required_until=rru,
        front_page_max=int(s.get("front_page_max", 18)),
    )
    tiers = {
        name: Tier(name=name, min_score=int(t.get("min_score", 5)), daily_cap=int(t.get("daily_cap", 5)))
        for name, t in (raw.get("tiers") or {}).items()
    }
    sources: list[Source] = []
    for src in raw.get("sources") or []:
        if src.get("scope") not in tiers:
            log(f"[config] source {src.get('id')} has unknown scope {src.get('scope')!r}, skipped")
            continue
        sources.append(Source(
            id=str(src["id"]),
            label=str(src.get("label", src["id"])),
            type=str(src["type"]),
            scope=str(src["scope"]),
            category=str(src.get("category", "news")),
            url=str(src.get("url", "")),
            query=str(src.get("query", "")),
            partner=src.get("partner"),
            min_score=src.get("min_score"),
        ))
    blocklist = [str(d).lower() for d in (raw.get("blocklist_domains") or [])]
    return Config(settings=settings, tiers=tiers, sources=sources, blocklist=blocklist)


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except json.JSONDecodeError:
            pass
    return {"seen_urls": [], "seen_fingerprints": [], "daily_counts": {}}


def save_state(state: dict) -> None:
    # Prune daily counts older than two weeks so the file stays small.
    cutoff = (date.today() - timedelta(days=14)).isoformat()
    state["daily_counts"] = {d: c for d, c in state.get("daily_counts", {}).items() if d >= cutoff}
    state["seen_urls"] = sorted(set(state.get("seen_urls", [])))[-5000:]
    state["seen_fingerprints"] = sorted(set(state.get("seen_fingerprints", [])))[-5000:]
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n")


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
    dropped = {
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "src", "cmpid",
    }
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parsed.query) if k.lower() not in dropped]
    return urllib.parse.urlunsplit((
        parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, urllib.parse.urlencode(query), "",
    ))


def safe_domain(url: str) -> str:
    try:
        host = urllib.parse.urlsplit(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def domain_matches(domain: str, patterns: list[str]) -> bool:
    return any(domain == p or domain.endswith("." + p) for p in patterns)


def make_fingerprint(title: str, municipalities: list[str]) -> str:
    """Same format as the Monday pipeline: normalized title + first municipality."""
    t = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
    t = re.sub(r"[^\w\s]", " ", t).lower()
    t = re.sub(r"\s+", " ", t).strip()
    muni = (municipalities[0] if municipalities else "").lower()
    return f"{t}|{muni}"


def strip_html(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def load_existing_fingerprints() -> set[str]:
    fps: set[str] = set()
    if not ITEMS_DIR.exists():
        return fps
    for md in ITEMS_DIR.glob("*.md"):
        text = md.read_text(errors="ignore")
        m = re.search(r"^fingerprint:\s*\"?([^\"\n]+)\"?\s*$", text, re.MULTILINE)
        if m:
            fps.add(m.group(1).strip())
    return fps


def load_existing_urls() -> set[str]:
    urls: set[str] = set()
    if not ITEMS_DIR.exists():
        return urls
    for md in ITEMS_DIR.glob("*.md"):
        text = md.read_text(errors="ignore")
        m = re.search(r"^source_url:\s*\"?([^\"\n]+)\"?\s*$", text, re.MULTILINE)
        if m:
            urls.add(normalize_url(m.group(1).strip()))
    return urls


def load_registry_fingerprints() -> set[str]:
    """Fingerprints from the Monday pipeline's item-registry.json, if present."""
    if not ITEM_REGISTRY_PATH.exists():
        return set()
    try:
        data = json.loads(ITEM_REGISTRY_PATH.read_text())
    except json.JSONDecodeError:
        return set()
    items = data.get("items", data) if isinstance(data, dict) else {}
    return {str(k) for k in items.keys()} if isinstance(items, dict) else set()


def robots_allowed(url: str, cache: dict[str, Any]) -> bool:
    parsed = urllib.parse.urlsplit(url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    if root not in cache:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(f"{root}/robots.txt")
        try:
            rp.read()
        except Exception:
            rp = None  # unreachable robots.txt: treat as allowed
        cache[root] = rp
    rp = cache[root]
    if rp is None:
        return True
    try:
        return rp.can_fetch(USER_AGENT, url)
    except Exception:
        return True


# -----------------------------------------------------------------------------
# Collection
# -----------------------------------------------------------------------------

@dataclass
class Candidate:
    title: str
    url: str
    published_at: datetime
    source: Source
    snippet: str = ""
    feed_body: str = ""          # full text when the feed carries it (WordPress content:encoded)
    publisher_name: str = ""     # from Google News <source>, or the feed title
    source_domain: str = ""
    partner: str | None = None
    score: int = 0
    score_reason: str = ""
    kill_reason: str = ""

    @property
    def tier(self) -> str:
        return self.source.scope

    @property
    def min_score_override(self) -> int | None:
        return self.source.min_score


def parse_published(entry: dict) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            try:
                return datetime(*t[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    return None


def fetch_feed(src: Source, max_age_days: int = 3) -> tuple[str, list[dict]]:
    if src.type == "rss_feed":
        feed = feedparser.parse(src.url, agent=BROWSER_UA)
    elif src.type == "google_news_query":
        # Google News ranks by relevance, not date, so ask it for recent items
        # explicitly; otherwise most results are months old and get dropped.
        q = urllib.parse.quote_plus(f"{src.query} when:{max_age_days}d")
        feed = feedparser.parse(
            f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en", agent=BROWSER_UA,
        )
    else:
        raise ValueError(f"unknown source type {src.type!r}")
    if getattr(feed, "bozo", False) and not feed.entries:
        raise RuntimeError(f"feed error: {getattr(feed, 'bozo_exception', 'unparseable')}")
    feed_title = str(feed.feed.get("title", "")) if getattr(feed, "feed", None) else ""
    return feed_title, list(feed.entries or [])


def resolve_google_news(url: str) -> str | None:
    """Google News RSS links point at news.google.com. Resolve to the publisher."""
    if "news.google.com" not in url:
        return url
    if gnewsdecoder is None:
        return None
    try:
        out = gnewsdecoder(url, interval=1)
        if out and out.get("status") and out.get("decoded_url"):
            return str(out["decoded_url"])
    except Exception:
        pass
    return None


def collect_candidates(cfg: Config, state: dict, only_source: str | None, only_tier: str | None) -> tuple[list[Candidate], dict]:
    seen_urls = set(state.get("seen_urls", [])) | load_existing_urls()
    cutoff = datetime.now(timezone.utc) - timedelta(days=cfg.settings.max_age_days)
    stats = {"fetched": 0, "too_old": 0, "blocklist": 0, "title_kill": 0, "dupe_url": 0, "unresolved": 0, "feed_errors": 0}
    out: list[Candidate] = []
    batch_seen: set[str] = set()

    for src in cfg.sources:
        if only_source and src.id != only_source:
            continue
        if only_tier and src.scope != only_tier:
            continue
        try:
            feed_title, entries = fetch_feed(src, cfg.settings.max_age_days)
        except Exception as e:
            log(f"[feed-error] {src.id}: {e}")
            stats["feed_errors"] += 1
            continue

        taken = 0
        for entry in entries:
            if taken >= cfg.settings.max_items_per_source:
                break
            stats["fetched"] += 1
            title = strip_html(entry.get("title") or "")
            link = entry.get("link") or entry.get("id") or ""
            if not title or not link:
                continue

            published = parse_published(entry)
            if published and published < cutoff:
                stats["too_old"] += 1
                continue

            if any(p.search(title) for p in TITLE_KILL_PATTERNS):
                stats["title_kill"] += 1
                continue

            publisher_name = feed_title
            if src.type == "google_news_query":
                gsrc = entry.get("source") or {}
                publisher_name = str(gsrc.get("title") or "")
                # Google News titles end with " - Publisher"; strip it.
                title = re.sub(r"\s+-\s+[^-]+$", "", title).strip() or title
                resolved = resolve_google_news(link)
                if not resolved:
                    stats["unresolved"] += 1
                    continue
                link = resolved

            url = normalize_url(link)
            domain = safe_domain(url)
            if not domain or "news.google.com" in domain:
                stats["unresolved"] += 1
                continue
            if domain_matches(domain, cfg.blocklist) or domain.endswith(FOREIGN_TLDS):
                stats["blocklist"] += 1
                continue
            if url in seen_urls or url in batch_seen:
                stats["dupe_url"] += 1
                continue
            batch_seen.add(url)

            snippet = strip_html(entry.get("summary") or entry.get("description") or "")[:700]
            feed_body = ""
            content = entry.get("content")
            if content and isinstance(content, list):
                feed_body = strip_html(content[0].get("value", ""))

            partner = src.partner or PARTNER_DOMAINS.get(domain)
            out.append(Candidate(
                title=title,
                url=url,
                published_at=published or datetime.now(timezone.utc),
                source=src,
                snippet=snippet,
                feed_body=feed_body,
                publisher_name=publisher_name,
                source_domain=domain,
                partner=partner,
            ))
            taken += 1

    return out, stats


# -----------------------------------------------------------------------------
# HTTP fetch
# -----------------------------------------------------------------------------

def fetch_body(client: httpx.Client, url: str, retries: int = 3) -> tuple[str, str]:
    """Return (body_text, final_url) or ('', url) if unfetchable."""
    backoff = 1.5
    for attempt in range(retries):
        try:
            r = client.get(url, follow_redirects=True, timeout=HTTP_TIMEOUT, headers={"User-Agent": USER_AGENT})
            if r.status_code in (429, 503):
                time.sleep(backoff * (attempt + 1) + random.random())
                continue
            r.raise_for_status()
            doc = ReadabilityDocument(r.text)
            text = strip_html(doc.summary(html_partial=True))
            return text, str(r.url)
        except Exception:
            if attempt == retries - 1:
                return "", url
            time.sleep(backoff * (attempt + 1) + random.random())
    return "", url


# -----------------------------------------------------------------------------
# Anthropic calls
# -----------------------------------------------------------------------------

class Usage:
    """Token counter so the run summary can show what the day cost."""

    def __init__(self) -> None:
        self.by_model: dict[str, list[int]] = {}

    def add(self, model: str, resp: Any) -> None:
        u = getattr(resp, "usage", None)
        if not u:
            return
        cur = self.by_model.setdefault(model, [0, 0])
        cur[0] += int(getattr(u, "input_tokens", 0) or 0) + int(getattr(u, "cache_read_input_tokens", 0) or 0)
        cur[1] += int(getattr(u, "output_tokens", 0) or 0)

    def cost(self) -> float:
        total = 0.0
        for model, (inp, out) in self.by_model.items():
            pin, pout = PRICE_PER_M.get(model, (0.0, 0.0))
            total += inp / 1e6 * pin + out / 1e6 * pout
        return total

    def summary(self) -> str:
        parts = [f"{m}: {i:,} in / {o:,} out" for m, (i, o) in self.by_model.items()]
        return "; ".join(parts) + f"; est. ${self.cost():.3f}"


def call_with_retry(fn, what: str, attempts: int = 3):
    for i in range(attempts):
        try:
            return fn()
        except RateLimitError:
            wait = 10 * (i + 1)
            log(f"[rate-limit] {what}: waiting {wait}s")
            time.sleep(wait)
        except APIConnectionError as e:
            if i == attempts - 1:
                raise
            log(f"[connection] {what}: {e}; retrying")
            time.sleep(3 * (i + 1))
        except APIStatusError as e:
            # 4xx other than 429 will not succeed on retry.
            raise RuntimeError(f"{what}: API error {getattr(e, 'status_code', '?')}: {e}") from e
    raise RuntimeError(f"{what}: gave up after {attempts} attempts")


def parse_json_text(resp: Any) -> dict:
    text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), "")
    return json.loads(text)


def score_candidate(client: Any, usage: Usage, c: Candidate) -> tuple[int, bool, str, str]:
    rubric = TIER_RUBRICS.get(c.tier, TIER_RUBRICS["local"])
    partner_note = (
        "\nThis item is PARTNER CONTENT from a publisher who has granted reuse rights. Educational explainers are welcome; company announcements and pricing news are not.\n"
        if c.partner else ""
    )
    user = (
        f"{rubric}\n{partner_note}\n"
        f"HEADLINE: {c.title}\n"
        f"PUBLISHER: {c.publisher_name or c.source_domain}\n"
        f"PUBLISHED: {c.published_at.date().isoformat()}\n"
        f"FEED SNIPPET: {c.snippet or '(none)'}\n"
    )
    resp = call_with_retry(
        lambda: client.messages.create(
            model=MODEL_SCORER,
            max_tokens=400,
            system=SYSTEM_PROMPT_SCORER,
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": SCORER_SCHEMA}},
        ),
        what=f"score {c.url}",
    )
    usage.add(MODEL_SCORER, resp)
    data = parse_json_text(resp)
    return int(data["relevance_score"]), bool(data["kill"]), str(data.get("kill_reason", "")), str(data.get("reason", ""))


def rewrite_candidate(client: Any, usage: Usage, c: Candidate, body: str) -> dict:
    partner_note = ""
    if c.partner:
        partner_note = (
            "\nPARTNER CONTENT: the publisher has granted BRIC permission to reproduce this material. "
            "Write it fully in BRIC's voice as an educational piece for Columbus landlords and investors. "
            "The detail may run 250 to 350 words. Do not name the partner company in the summary or detail; "
            "the site labels partner content and adds the partner link itself.\n"
        )
    user = (
        f"Rewrite this source into the BRIC.News format.\n{partner_note}\n"
        f"TIER: {c.tier}\n"
        f"SOURCE HEADLINE: {c.title}\n"
        f"PUBLISHER: {c.publisher_name or c.source_domain}\n"
        f"SOURCE URL: {c.url}\n"
        f"PUBLISHED: {c.published_at.date().isoformat()}\n"
        f"CATEGORY HINT: {c.source.category}\n\n"
        f"SOURCE TEXT:\n{body[:9000]}\n"
    )
    resp = call_with_retry(
        lambda: client.messages.create(
            model=MODEL_WRITER,
            max_tokens=4000,
            system=SYSTEM_PROMPT_WRITER,
            messages=[{"role": "user", "content": user}],
            output_config={
                "effort": "medium",
                "format": {"type": "json_schema", "schema": WRITER_SCHEMA},
            },
        ),
        what=f"rewrite {c.url}",
    )
    usage.add(MODEL_WRITER, resp)
    return parse_json_text(resp)


# -----------------------------------------------------------------------------
# Quality gates and markdown output
# -----------------------------------------------------------------------------

def trim_to_limit(text: str, limit: int) -> str:
    """Cut at a sentence boundary under `limit` characters."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    for sep in (". ", "! ", "? "):
        idx = cut.rfind(sep)
        if idx > limit * 0.5:
            return cut[: idx + 1].strip()
    return cut.rsplit(" ", 1)[0].rstrip(",;:") + "."


def why_is_generic(why: str, specificity: int) -> bool:
    if specificity < 3:
        return True
    if len(why.strip()) < 60:
        return True
    return any(p.search(why) for p in GENERIC_WHY_PATTERNS)


def scrub_style(s: str) -> str:
    """House style: no em dashes."""
    return s.replace("—", ", ").replace(" , ", ", ").replace(",,", ",")


def yaml_str(s: str) -> str:
    # json.dumps produces a valid double-quoted YAML scalar.
    return json.dumps(s, ensure_ascii=False)


def yaml_list(items: list[str]) -> str:
    return "[" + ", ".join(yaml_str(i) for i in items) + "]"


def to_markdown(rec: dict) -> str:
    lines = ["---"]
    lines.append(f"title: {yaml_str(rec['title'])}")
    lines.append(f"summary: {yaml_str(rec['summary'])}")
    lines.append(f"detail: {yaml_str(rec['detail'])}")
    lines.append(f"why_it_matters: {yaml_str(rec['why_it_matters'])}")
    lines.append(f"source_url: {yaml_str(rec['source_url'])}")
    lines.append(f"source_domain: {yaml_str(rec['source_domain'])}")
    lines.append(f"source_name: {yaml_str(rec['source_name'])}")
    lines.append(f"published_at: {rec['published_at']}")
    lines.append(f"scope: {yaml_str(rec['scope'])}")
    lines.append(f"topics: {yaml_list(rec['topics'])}")
    lines.append(f"municipalities: {yaml_list(rec['municipalities'])}")
    lines.append(f"content_type: {yaml_str(rec['content_type'])}")
    lines.append(f"entities: {yaml_list(rec['entities'])}")
    if rec.get("partner"):
        lines.append(f"partner: {yaml_str(rec['partner'])}")
    lines.append(f"fingerprint: {yaml_str(rec['fingerprint'])}")
    lines.append(f"risk_flags: {yaml_list(rec['risk_flags'])}")
    lines.append(f"relevance_score: {rec['relevance_score']}")
    lines.append("featured: false")
    lines.append(f"ingested_at: {rec['ingested_at']}")
    lines.append(f"ingest_model: {yaml_str(rec['ingest_model'])}")
    lines.append(f"ingest_prompt_version: {yaml_str(rec['ingest_prompt_version'])}")
    lines.append(f"ingest_source_id: {yaml_str(rec['ingest_source_id'])}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def write_item(rec: dict) -> Path:
    ITEMS_DIR.mkdir(parents=True, exist_ok=True)
    date_part = rec["published_at"]
    slug = slugify(rec["title"])[:60] or "untitled"
    path = ITEMS_DIR / f"{date_part}-{slug}.md"
    n = 1
    while path.exists():
        path = ITEMS_DIR / f"{date_part}-{slug}-{n}.md"
        n += 1
    path.write_text(to_markdown(rec))
    return path


# -----------------------------------------------------------------------------
# Agent mode
#
# The same pipeline, driven step by step by a Claude Code session instead of
# API calls, so the daily run can use the publisher's Claude plan rather than
# an API key. The session does the judgment (scoring, writing); this file
# still enforces every rule (caps, kill patterns, generic why-it-matters,
# dedupe, robots, paywalls). See scripts/AGENT_RUN.md for the procedure.
#
#   --collect-json PATH      collect + filter, write candidates JSON, no AI
#   --fetch URL              print the extracted article body as JSON
#   --write-items PATH       take the session's written records, gate, write
#                            markdown, update the ledger, write the run report
# -----------------------------------------------------------------------------

def agent_collect(cfg: Config, state: dict, out_path: Path, only_source: str | None, only_tier: str | None, mode: str) -> int:
    candidates, stats = collect_candidates(cfg, state, only_source, only_tier)
    today = date.today().isoformat()
    daily = state.get("daily_counts", {}).get(today, {})
    payload = {
        "date": today,
        "mode": mode,
        "prompt_version": PROMPT_VERSION,
        "tiers": {
            name: {
                "min_score": t.min_score,
                "daily_cap": t.daily_cap,
                "open_slots": max(0, t.daily_cap - int(daily.get(name, 0))),
                "rubric": TIER_RUBRICS.get(name, TIER_RUBRICS["local"]),
            }
            for name, t in cfg.tiers.items()
        },
        "scorer_instructions": SYSTEM_PROMPT_SCORER,
        "writer_instructions": SYSTEM_PROMPT_WRITER,
        "candidates": [
            {
                "id": i,
                "tier": c.tier,
                "min_score": c.min_score_override or cfg.tiers[c.tier].min_score,
                "title": c.title,
                "url": c.url,
                "source_domain": c.source_domain,
                "publisher_name": c.publisher_name,
                "published_at": c.published_at.date().isoformat(),
                "source_id": c.source.id,
                "category_hint": c.source.category,
                "partner": c.partner,
                "snippet": c.snippet,
                "has_feed_body": bool(c.feed_body),
                "feed_body": c.feed_body if c.partner else "",
            }
            for i, c in enumerate(candidates)
        ],
        "stats": stats,
    }
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    log(f"[collect] {len(candidates)} candidates written to {out_path}")
    for name, t in payload["tiers"].items():
        n = sum(1 for c in candidates if c.tier == name)
        log(f"  {name:9s} bar {t['min_score']}  open slots {t['open_slots']}  candidates {n}")
    return 0


def agent_fetch(cfg: Config, url: str) -> int:
    domain = safe_domain(url)
    result: dict[str, Any] = {"url": url, "ok": False, "reason": "", "chars": 0, "body": ""}
    if domain_matches(domain, cfg.blocklist):
        result["reason"] = "blocklisted domain"
    else:
        robots_cache: dict[str, Any] = {}
        partner = PARTNER_DOMAINS.get(domain)
        if not partner and not robots_allowed(url, robots_cache):
            result["reason"] = "robots.txt disallows"
        else:
            with httpx.Client() as http:
                body, _ = fetch_body(http, url)
            if not body:
                result["reason"] = "fetch failed"
            elif len(body) < PAYWALL_BODY_CHAR_THRESHOLD and "subscribe" in body.lower():
                result["reason"] = "paywall"
            elif len(body) < cfg.settings.min_body_chars:
                result["reason"] = f"thin page ({len(body)} chars)"
            else:
                result.update(ok=True, chars=len(body), body=body[:9000])
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


def agent_write(cfg: Config, state: dict, items_path: Path, seen_path: Path | None, mode: str, model_name: str) -> int:
    """Gate and write records the session produced. Same rules as the API path."""
    try:
        records = json.loads(items_path.read_text())
    except Exception as e:
        log(f"[err] cannot read {items_path}: {e}")
        return 1
    if isinstance(records, dict):
        records = records.get("items", [])

    known_fps = set(state.get("seen_fingerprints", [])) | load_existing_fingerprints() | load_registry_fingerprints()
    seen_urls = set(state.get("seen_urls", []))
    today = date.today().isoformat()
    daily = state.setdefault("daily_counts", {}).setdefault(today, {})
    stats = {"submitted": len(records), "published": 0, "why_generic": 0, "dupe_fp": 0, "below_bar": 0, "cap": 0, "invalid": 0}
    published: list[dict] = []

    # Best first, so caps are filled by the strongest items.
    records.sort(key=lambda r: -int(r.get("relevance_score", 0) or 0))

    for r in records:
        tier_name = r.get("tier") if r.get("tier") in cfg.tiers else "local"
        tier = cfg.tiers[tier_name]
        required = ("title", "summary", "detail", "why_it_matters", "url", "source_domain", "published_at")
        if any(not r.get(k) for k in required):
            stats["invalid"] += 1
            log(f"[invalid] missing fields: {r.get('title', '?')[:60]}")
            continue
        score = int(r.get("relevance_score", 0) or 0)
        bar = int(r.get("min_score") or tier.min_score)
        if score < bar:
            stats["below_bar"] += 1
            log(f"[low] {score} < {bar}: {r['title'][:60]}")
            continue
        if int(daily.get(tier_name, 0)) >= tier.daily_cap:
            stats["cap"] += 1
            log(f"[cap] {tier_name} full: {r['title'][:60]}")
            continue
        why = scrub_style(str(r["why_it_matters"]).strip())
        if why_is_generic(why, int(r.get("why_specificity", 3) or 3)):
            stats["why_generic"] += 1
            log(f"[why-generic] {r['title'][:60]} :: {why[:70]}")
            continue
        munis = [m for m in r.get("municipalities", []) if m in MUNICIPALITIES] if tier_name == "local" else []
        title = scrub_style(str(r["title"]).strip())[:120]
        fp = make_fingerprint(title, munis)
        if fp in known_fps:
            stats["dupe_fp"] += 1
            log(f"[dupe-fp] {title[:60]}")
            continue
        content_type = r.get("content_type") if r.get("content_type") in CONTENT_TYPES else "news"
        domain = safe_domain(r["url"]) or str(r["source_domain"])
        partner = r.get("partner") or PARTNER_DOMAINS.get(domain)
        rec = {
            "title": title,
            "summary": trim_to_limit(scrub_style(str(r["summary"])), 600),
            "detail": trim_to_limit(scrub_style(str(r["detail"])), 2000),
            "why_it_matters": why,
            "source_url": normalize_url(str(r["url"])),
            "source_domain": domain,
            "source_name": str(r.get("source_name") or r.get("publisher_name") or domain).strip(),
            "published_at": str(r["published_at"])[:10],
            "scope": tier_name,
            "topics": [t for t in r.get("topics", []) if t in TOPICS][:5],
            "municipalities": munis,
            "content_type": content_type,
            "entities": [e for e in r.get("entities", []) if isinstance(e, str)][:8],
            "partner": partner,
            "fingerprint": fp,
            "risk_flags": [x for x in r.get("risk_flags", []) if isinstance(x, str)][:5],
            "relevance_score": max(1, min(10, score)),
            "ingested_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ingest_model": model_name,
            "ingest_prompt_version": PROMPT_VERSION,
            "ingest_source_id": str(r.get("source_id", "agent")),
        }
        try:
            path = write_item(rec)
        except Exception as e:
            stats["invalid"] += 1
            log(f"[write-error] {title[:60]}: {e}")
            continue
        known_fps.add(fp)
        seen_urls.add(rec["source_url"])
        daily[tier_name] = int(daily.get(tier_name, 0)) + 1
        rec["file"] = str(path.relative_to(REPO_ROOT))
        published.append(rec)
        stats["published"] += 1
        log(f"[published] {tier_name:8s} {score:2d} {path.name}")

    # Everything the session looked at counts as seen, published or not.
    if seen_path and seen_path.exists():
        try:
            cand = json.loads(seen_path.read_text()).get("candidates", [])
            for c in cand:
                if c.get("url"):
                    seen_urls.add(c["url"])
            stats["marked_seen"] = len(cand)
            stats["candidates"] = len(cand)
        except Exception:
            pass
    state["seen_urls"] = sorted(seen_urls)
    state["seen_fingerprints"] = sorted(known_fps)
    save_state(state)
    write_run_report(mode, published, stats, None, dry_run=False)
    log(f"[done] {len(published)} published, report in {LAST_RUN_MD.name}")
    return 0


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def decide_mode(cfg: Config, override: str | None) -> str:
    if override in ("review", "auto"):
        return override
    until = cfg.settings.review_required_until
    if until and date.today() < until:
        return "review"
    return "auto"


def write_run_report(mode: str, published: list[dict], stats: dict, usage: Usage | None, dry_run: bool) -> None:
    today = date.today().isoformat()
    report = {
        "date": today,
        "mode": mode,
        "dry_run": dry_run,
        "published": published,
        "stats": stats,
        "usage": usage.summary() if usage else "",
        "prompt_version": PROMPT_VERSION,
    }
    LAST_RUN_JSON.write_text(json.dumps(report, indent=2) + "\n")

    md: list[str] = []
    md.append(f"## Daily feed for {today}")
    md.append("")
    if dry_run:
        md.append("_Dry run: nothing was scored or written._")
        md.append("")
    md.append(f"**{len(published)} item(s)** · mode: `{mode}` · {stats.get('candidates', 0)} candidates collected")
    md.append("")
    if published:
        md.append("| Tier | Score | Title | Source |")
        md.append("|---|---|---|---|")
        for p in published:
            md.append(f"| {p['scope']} | {p['relevance_score']} | {p['title']} | [{p['source_name']}]({p['source_url']}) |")
        md.append("")
        for p in published:
            md.append(f"### {p['title']}")
            md.append("")
            md.append(f"_{p['scope']} · score {p['relevance_score']} · {p['source_name']} · `{p['file']}`_")
            md.append("")
            md.append(p["summary"])
            md.append("")
            md.append(f"**Why it matters:** {p['why_it_matters']}")
            if p.get("risk_flags"):
                md.append("")
                md.append("**Flags for review:** " + "; ".join(p["risk_flags"]))
            md.append("")
    md.append("<details><summary>Run stats</summary>")
    md.append("")
    for k, v in stats.items():
        md.append(f"- {k}: {v}")
    if usage:
        md.append(f"- tokens: {usage.summary()}")
    md.append("")
    md.append("</details>")
    LAST_RUN_MD.write_text("\n".join(md) + "\n")

    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a") as f:
            f.write("\n".join(md) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="BRIC.News syndication ingest")
    ap.add_argument("--dry-run", action="store_true", help="Collect and filter only. No API calls, no writes.")
    ap.add_argument("--limit", type=int, default=0, help="Cap total items written this run (0 = tier caps only)")
    ap.add_argument("--source", help="Run a single source id")
    ap.add_argument("--tier", choices=SCOPES, help="Run a single tier")
    ap.add_argument("--mode", choices=["review", "auto"], help="Override review/auto decision")
    ap.add_argument("--max-age-days", type=int, help="Override settings.max_age_days")
    ap.add_argument("--collect-json", metavar="PATH", help="Agent mode: collect + filter, write candidates JSON, no AI")
    ap.add_argument("--fetch", metavar="URL", help="Agent mode: print the extracted article body as JSON")
    ap.add_argument("--write-items", metavar="PATH", help="Agent mode: gate and write the session's records")
    ap.add_argument("--mark-seen", metavar="PATH", help="With --write-items: candidates JSON whose URLs count as seen")
    ap.add_argument("--model-name", default="claude-code-session", help="Recorded as ingest_model in agent mode")
    args = ap.parse_args()

    if not SOURCES_PATH.exists():
        log(f"[err] missing {SOURCES_PATH}")
        return 1
    cfg = load_config()
    if args.max_age_days:
        cfg.settings.max_age_days = args.max_age_days
    mode = decide_mode(cfg, args.mode)

    state = load_state()

    if args.fetch:
        return agent_fetch(cfg, args.fetch)
    if args.collect_json:
        return agent_collect(cfg, state, Path(args.collect_json), args.source, args.tier, mode)
    if args.write_items:
        return agent_write(cfg, state, Path(args.write_items), Path(args.mark_seen) if args.mark_seen else None, mode, args.model_name)

    known_fps = set(state.get("seen_fingerprints", [])) | load_existing_fingerprints() | load_registry_fingerprints()
    today = date.today().isoformat()
    daily = state.setdefault("daily_counts", {}).setdefault(today, {})

    log(f"[info] {len(cfg.sources)} sources, {len(cfg.tiers)} tiers, mode={mode}, prompt={PROMPT_VERSION}")

    # 1. COLLECT
    candidates, stats = collect_candidates(cfg, state, args.source, args.tier)
    stats["candidates"] = len(candidates)
    log(
        f"[collect] {len(candidates)} candidates from {stats['fetched']} entries "
        f"({stats['too_old']} too old, {stats['blocklist']} blocklisted, {stats['title_kill']} title-killed, "
        f"{stats['dupe_url']} already seen, {stats['unresolved']} unresolved, {stats['feed_errors']} feed errors)"
    )

    if args.dry_run:
        by_tier: dict[str, list[Candidate]] = {}
        for c in candidates:
            by_tier.setdefault(c.tier, []).append(c)
        for tier, items in by_tier.items():
            t = cfg.tiers[tier]
            log(f"\n== {tier.upper()} (bar {t.min_score}, cap {t.daily_cap}/day) — {len(items)} candidates")
            for c in items:
                flag = " [partner]" if c.partner else ""
                log(f"  - {c.published_at.date()} {c.source_domain:28s} {c.title[:80]}{flag}")
        write_run_report(mode, [], stats, None, dry_run=True)
        return 0

    if Anthropic is None or not os.environ.get("ANTHROPIC_API_KEY"):
        log("[err] ANTHROPIC_API_KEY missing or anthropic SDK not installed. Use --dry-run to test without it.")
        return 2
    client = Anthropic()
    usage = Usage()

    # 2. SCORE (title + snippet only; no article fetch yet)
    stats.update({"scored": 0, "killed": 0, "below_bar": 0, "score_errors": 0})
    for c in candidates:
        try:
            score, kill, kill_reason, reason = score_candidate(client, usage, c)
        except Exception as e:
            log(f"[score-error] {c.url}: {e}")
            stats["score_errors"] += 1
            c.kill_reason = "score error"
            continue
        stats["scored"] += 1
        c.score, c.score_reason = score, reason
        bar = c.min_score_override or cfg.tiers[c.tier].min_score
        if kill:
            c.kill_reason = kill_reason or "killed by scorer"
            stats["killed"] += 1
            log(f"[kill] {c.tier:8s} {score:2d} {c.source_domain:26s} {c.title[:70]} ({c.kill_reason})")
        elif score < bar:
            c.kill_reason = f"below bar ({score} < {bar})"
            stats["below_bar"] += 1
            log(f"[low]  {c.tier:8s} {score:2d} {c.source_domain:26s} {c.title[:70]}")
        else:
            log(f"[pass] {c.tier:8s} {score:2d} {c.source_domain:26s} {c.title[:70]}")

    # 3. SELECT + 4. WRITE, per tier, filling caps from the top of the ranking.
    published: list[dict] = []
    stats.update({"fetch_failed": 0, "paywall": 0, "robots": 0, "thin": 0, "why_generic": 0, "dupe_fp": 0, "write_errors": 0})
    robots_cache: dict[str, Any] = {}
    seen_urls = set(state.get("seen_urls", []))
    total_written = 0

    with httpx.Client() as http:
        for tier_name, tier in cfg.tiers.items():
            if args.tier and tier_name != args.tier:
                continue
            already = int(daily.get(tier_name, 0))
            slots = max(0, tier.daily_cap - already)
            eligible = sorted(
                (c for c in candidates if c.tier == tier_name and not c.kill_reason),
                key=lambda c: (-c.score, -c.published_at.timestamp()),
            )
            if not eligible:
                continue
            log(f"\n[{tier_name}] {len(eligible)} eligible, {slots} slot(s) open today")

            for c in eligible:
                if slots <= 0:
                    log(f"[cap] {tier_name} cap reached; remaining eligible items wait for tomorrow's run")
                    break
                if args.limit and total_written >= args.limit:
                    break

                # Partner feeds carry their own full text; everyone else gets a polite fetch.
                if c.partner and c.feed_body:
                    body = c.feed_body
                else:
                    if not c.partner and not robots_allowed(c.url, robots_cache):
                        log(f"[robots] {c.url}")
                        stats["robots"] += 1
                        continue
                    body, _ = fetch_body(http, c.url)
                    if not body:
                        log(f"[fetch-fail] {c.url}")
                        stats["fetch_failed"] += 1
                        continue
                    if len(body) < PAYWALL_BODY_CHAR_THRESHOLD and "subscribe" in body.lower():
                        log(f"[paywall] {c.url}")
                        stats["paywall"] += 1
                        continue
                    if len(body) < cfg.settings.min_body_chars:
                        log(f"[thin] {len(body)} chars {c.url}")
                        stats["thin"] += 1
                        continue

                try:
                    out = rewrite_candidate(client, usage, c, body)
                except Exception as e:
                    log(f"[write-error] {c.url}: {e}")
                    stats["write_errors"] += 1
                    continue

                why = scrub_style(out["why_it_matters"].strip())
                if why_is_generic(why, int(out.get("why_specificity", 3))):
                    log(f"[why-generic] {c.title[:70]} :: {why[:80]}")
                    stats["why_generic"] += 1
                    continue

                munis = [m for m in out.get("municipalities", []) if m in MUNICIPALITIES] if tier_name == "local" else []
                title = scrub_style(out["title"].strip())[:120]
                fp = make_fingerprint(title, munis)
                if fp in known_fps:
                    log(f"[dupe-fp] {title[:70]}")
                    stats["dupe_fp"] += 1
                    continue

                content_type = out.get("content_type") if out.get("content_type") in CONTENT_TYPES else c.source.category
                if content_type not in CONTENT_TYPES:
                    content_type = "news"

                rec = {
                    "title": title,
                    "summary": trim_to_limit(scrub_style(out["summary"]), 600),
                    "detail": trim_to_limit(scrub_style(out["detail"]), 2000),
                    "why_it_matters": why,
                    "source_url": c.url,
                    "source_domain": c.source_domain,
                    "source_name": (out.get("source_name") or c.publisher_name or c.source_domain).strip(),
                    "published_at": c.published_at.date().isoformat(),
                    "scope": tier_name,
                    "topics": [t for t in out.get("topics", []) if t in TOPICS][:5],
                    "municipalities": munis,
                    "content_type": content_type,
                    "entities": [e for e in out.get("entities", []) if isinstance(e, str)][:8],
                    "partner": c.partner,
                    "fingerprint": fp,
                    "risk_flags": [r for r in out.get("risk_flags", []) if isinstance(r, str)][:5],
                    "relevance_score": c.score,
                    "ingested_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "ingest_model": MODEL_WRITER,
                    "ingest_prompt_version": PROMPT_VERSION,
                    "ingest_source_id": c.source.id,
                }
                try:
                    path = write_item(rec)
                except Exception as e:
                    log(f"[write-error] {c.url}: {e}")
                    stats["write_errors"] += 1
                    continue

                known_fps.add(fp)
                seen_urls.add(c.url)
                slots -= 1
                total_written += 1
                daily[tier_name] = int(daily.get(tier_name, 0)) + 1
                rec["file"] = str(path.relative_to(REPO_ROOT))
                published.append(rec)
                log(f"[published] {tier_name:8s} {c.score:2d} {path.name}")

    # Every candidate we looked at is "seen" now, published or not, so tomorrow's
    # run does not re-score the same headlines.
    for c in candidates:
        seen_urls.add(c.url)
    state["seen_urls"] = sorted(seen_urls)
    state["seen_fingerprints"] = sorted(known_fps)
    save_state(state)

    stats["published"] = len(published)
    write_run_report(mode, published, stats, usage, dry_run=False)
    log(f"\n[done] {len(published)} published · {usage.summary()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
