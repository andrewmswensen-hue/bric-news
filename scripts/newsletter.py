#!/usr/bin/env python3
"""
BRIC.News weekly newsletter draft builder.

Reads published items from /src/content/items/*.md (last 7 days, filtered by
featured=true OR relevance_score >= 7), builds a Beehiiv-compatible HTML email
with inline styles, and creates a DRAFT in Beehiiv via API. Does not send.

Env vars required:
  ANTHROPIC_API_KEY         (for intro paragraph)
  BEEHIIV_API_KEY           (for draft creation)
  BEEHIIV_PUBLICATION_ID    (for draft creation)

Prints the draft URL to stdout.
"""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import yaml

try:
    from anthropic import Anthropic
except ImportError:
    Anthropic = None

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
ITEMS_DIR = REPO_ROOT / "src" / "content" / "items"
VENDORS_DIR = REPO_ROOT / "src" / "content" / "vendors"
RESOURCES_DIR = REPO_ROOT / "src" / "content" / "resources"

INTRO_SYSTEM = """You write introductory paragraphs for the weekly BRIC.News newsletter, a Columbus, Ohio investor publication.

Voice: educational, supportive, value-driven. Like a knowledgeable Columbus investor friend. Never promotional. Never urgent. No CTAs.

Style rules: 60 to 80 words total. No em dashes. No AI buzzwords (game-changer, unlock, supercharge). No absolutes (always, never). Columbus-first framing. No mention of RL Property Management or any property management company unless they are a news subject.

Respond with ONLY the paragraph text. No headers, no quotation marks around it."""


def parse_frontmatter(text: str) -> tuple[dict, str]:
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.DOTALL)
    if not m:
        return {}, text
    fm = yaml.safe_load(m.group(1)) or {}
    return fm, m.group(2)


def load_items(since: datetime) -> list[dict]:
    items = []
    if not ITEMS_DIR.exists():
        return items
    for md in ITEMS_DIR.glob("*.md"):
        fm, _ = parse_frontmatter(md.read_text(errors="ignore"))
        if not fm:
            continue
        pub = fm.get("published_at")
        if isinstance(pub, str):
            try:
                pub_dt = datetime.fromisoformat(pub)
            except ValueError:
                pub_dt = datetime.strptime(pub[:10], "%Y-%m-%d")
        elif hasattr(pub, "year"):
            pub_dt = datetime(pub.year, pub.month, pub.day)
        else:
            continue
        if pub_dt.tzinfo is None:
            pub_dt = pub_dt.replace(tzinfo=timezone.utc)
        if pub_dt < since:
            continue
        fm["_published"] = pub_dt
        items.append(fm)
    return items


def load_collection_dir(dir_: Path) -> list[dict]:
    out = []
    if not dir_.exists():
        return out
    for md in dir_.glob("*.md"):
        fm, _ = parse_frontmatter(md.read_text(errors="ignore"))
        fm["_slug"] = md.stem
        out.append(fm)
    return out


def pick_for_newsletter(items: list[dict]) -> list[dict]:
    return [
        i for i in items
        if i.get("featured") is True or (i.get("relevance_score") or 0) >= 7
    ]


def generate_intro(client, num_items: int, num_policy: int) -> str:
    user_msg = (
        f"Write this week's newsletter intro paragraph. "
        f"We have {num_items} items total for readers, of which {num_policy} are policy updates. "
        "Acknowledge the week's themes at a high level without naming specific items. "
        "60 to 80 words. Educational, supportive tone."
    )
    msg = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=400,
        system=INTRO_SYSTEM,
        messages=[{"role": "user", "content": user_msg}],
    )
    return "".join(block.text for block in msg.content if getattr(block, "type", None) == "text").strip()


def render_item(item: dict) -> str:
    title = item.get("title", "")
    why = item.get("why_it_matters", "")
    summary = item.get("summary", "")
    if len(summary) > 260:
        summary = summary[:257].rstrip() + "…"
    url = item.get("source_url", "#")
    return f"""
<div style="margin-bottom:22px;padding-bottom:18px;border-bottom:1px solid #E5E0D8;">
  <h3 style="margin:0 0 6px 0;font-family:Georgia,serif;font-size:18px;color:#1F1F1F;line-height:1.3;">
    <a href="{url}" style="color:#1F1F1F;text-decoration:none;">{title}</a>
  </h3>
  <p style="margin:0 0 10px 0;font-size:14px;color:#1F1F1F;line-height:1.5;">{summary}</p>
  <p style="margin:0;font-size:14px;color:#4A4A4A;line-height:1.5;border-left:3px solid #B22222;padding-left:10px;">
    <strong style="color:#B22222;font-size:11px;text-transform:uppercase;letter-spacing:0.05em;">Why it matters: </strong>{why}
  </p>
</div>""".strip()


def render_vendor(vendor: dict) -> str:
    name = vendor.get("name", "")
    slug = vendor.get("_slug", "")
    cat = str(vendor.get("category", "")).replace("-", " ")
    desc = vendor.get("description", "")
    url = f"https://bric.news/vendors/{slug}"
    preferred = "<strong style=\"color:#B22222;\">Preferred · </strong>" if vendor.get("preferred") else ""
    return f"""
<div style="margin-bottom:16px;">
  <div style="font-size:11px;text-transform:uppercase;color:#4A4A4A;letter-spacing:0.05em;">{cat}</div>
  <h3 style="margin:4px 0;font-family:Georgia,serif;font-size:17px;">
    <a href="{url}" style="color:#1F1F1F;text-decoration:none;">{preferred}{name}</a>
  </h3>
  <p style="margin:0;font-size:14px;color:#1F1F1F;line-height:1.5;">{desc}</p>
</div>""".strip()


def render_resource(res: dict) -> str:
    return f"""
<div style="margin-bottom:16px;">
  <h3 style="margin:4px 0;font-family:Georgia,serif;font-size:17px;">
    <a href="{res.get("url", "#")}" style="color:#1F1F1F;">{res.get("label", "")}</a>
  </h3>
  <p style="margin:0;font-size:14px;color:#1F1F1F;line-height:1.5;">{res.get("description", "")}</p>
</div>""".strip()


def section(heading: str, inner_html: str) -> str:
    return f"""
<h2 style="font-family:Georgia,serif;font-size:20px;color:#B22222;margin:30px 0 12px 0;border-bottom:2px solid #E5E0D8;padding-bottom:6px;">{heading}</h2>
{inner_html}
""".strip()


def iso_week(d: datetime) -> int:
    return d.isocalendar()[1]


def build_html(intro: str, policy_items: list[dict], news_items: list[dict],
               vendor_spot: dict | None, resource_spot: dict | None,
               event_items: list[dict]) -> str:
    parts: list[str] = [
        '<div style="max-width:600px;margin:0 auto;padding:24px;background:#FAF9F6;font-family:-apple-system,Segoe UI,sans-serif;color:#1F1F1F;">',
        '<div style="text-align:center;margin-bottom:24px;">',
        '<h1 style="font-family:Georgia,serif;font-size:30px;color:#B22222;margin:0;letter-spacing:-0.02em;">BRIC<span style="color:#1F1F1F;">.</span>NEWS</h1>',
        '<p style="margin:4px 0;font-size:13px;color:#4A4A4A;font-style:italic;">Buy. Rent. Invest. Columbus.</p>',
        '</div>',
        f'<p style="font-size:15px;line-height:1.6;margin:18px 0 30px 0;">{intro}</p>',
    ]

    if policy_items:
        parts.append(section("This Week in Policy", "\n".join(render_item(i) for i in policy_items[:3])))
    if news_items:
        parts.append(section("This Week's News", "\n".join(render_item(i) for i in news_items[:3])))
    if event_items:
        parts.append(section("Events This Week", "\n".join(render_item(i) for i in event_items)))
    if vendor_spot:
        parts.append(section("Vendor Spotlight", render_vendor(vendor_spot)))
    if resource_spot:
        parts.append(section("Featured Resource", render_resource(resource_spot)))

    parts.extend([
        '<hr style="border:0;border-top:1px solid #E5E0D8;margin:32px 0 16px 0;">',
        '<p style="font-size:12px;color:#4A4A4A;line-height:1.6;text-align:center;">',
        "You're reading BRIC.News. Forward to a Columbus investor who'd benefit. ",
        'Reply to this email with corrections or vendor submissions.',
        '</p>',
        '<p style="font-size:12px;color:#4A4A4A;text-align:center;margin-top:14px;">',
        '<a href="https://bric.news" style="color:#B22222;">View on BRIC.News</a> · ',
        '<a href="https://bric.news/vendors" style="color:#B22222;">Submit a vendor</a>',
        '</p>',
        '</div>',
    ])
    return "\n".join(parts)


def create_beehiiv_draft(html: str, subject: str, publication_id: str, api_key: str) -> str:
    """Create a draft post in Beehiiv. Returns the editor/preview URL."""
    api_url = f"https://api.beehiiv.com/v2/publications/{publication_id}/posts"
    payload = {
        "title": subject,
        "subtitle": "Columbus investor news, vendors, and resources.",
        "body_content": html,
        "status": "draft",
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=30.0) as client:
        r = client.post(api_url, json=payload, headers=headers)
        r.raise_for_status()
        data = r.json()
    post_id = (data.get("data") or data).get("id", "")
    return f"https://app.beehiiv.com/posts/{post_id}" if post_id else "(draft created; id unknown)"


def main() -> int:
    since = datetime.now(timezone.utc) - timedelta(days=7)
    items = load_items(since)
    picks = pick_for_newsletter(items)

    if not picks:
        print("[info] no items qualify for this week's newsletter (none featured or score >= 7 in last 7 days)")
        return 0

    policy = sorted(
        [i for i in picks if i.get("content_type") == "policy"],
        key=lambda i: (-i.get("relevance_score", 0), i["_published"]),
        reverse=False,
    )
    news = sorted(
        [i for i in picks if i.get("content_type") in ("news", "market_data")],
        key=lambda i: (-i.get("relevance_score", 0), i["_published"]),
        reverse=False,
    )
    events = sorted(
        [i for i in items if i.get("content_type") == "event"],
        key=lambda i: i["_published"],
    )

    vendors = load_collection_dir(VENDORS_DIR)
    resources = load_collection_dir(RESOURCES_DIR)
    week = iso_week(datetime.now(timezone.utc))
    vendor_spot = vendors[week % len(vendors)] if vendors else None
    resource_spot = resources[week % len(resources)] if resources else None

    # Intro
    if not os.environ.get("ANTHROPIC_API_KEY") or Anthropic is None:
        intro = "This week's BRIC covers the latest Columbus-metro policy movement, market data releases, and a few items worth putting on your watchlist. Scroll for the full picks. As always, reply if you spot something we should be tracking."
    else:
        client = Anthropic()
        try:
            intro = generate_intro(client, len(picks), len(policy))
        except Exception as e:
            print(f"[warn] intro generation failed: {e}")
            intro = "This week's BRIC covers the latest Columbus-metro policy movement, market data releases, and a few items worth putting on your watchlist."

    html = build_html(intro, policy, news, vendor_spot, resource_spot, events[:3])
    subject = f"BRIC weekly: {datetime.now(timezone.utc).strftime('%B %-d, %Y')}"

    api_key = os.environ.get("BEEHIIV_API_KEY")
    pub_id = os.environ.get("BEEHIIV_PUBLICATION_ID")
    if not api_key or not pub_id:
        print("[warn] BEEHIIV_API_KEY / BEEHIIV_PUBLICATION_ID not set — skipping Beehiiv upload")
        print(f"[info] rendered HTML length: {len(html)} chars, {len(picks)} picks")
        out = SCRIPT_DIR / "newsletter-preview.html"
        out.write_text(html)
        print(f"[info] preview written to {out}")
        return 0

    try:
        draft_url = create_beehiiv_draft(html, subject, pub_id, api_key)
        print(f"[done] Beehiiv draft created: {draft_url}")
    except httpx.HTTPError as e:
        print(f"[err] Beehiiv API call failed: {e}")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
