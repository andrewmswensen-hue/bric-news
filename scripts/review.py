#!/usr/bin/env python3
"""
BRIC.News review tool.

Minimal Flask app that lists pending JSON items in /scripts/queue/,
lets you edit the summary/why-it-matters/topics/municipalities/content_type
/featured flag, then APPROVE (write to /src/content/items/*.md),
REJECT (move to queue/rejected/ with a reason), or SKIP.

Run: python scripts/review.py
Open: http://localhost:4001
"""

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import date, datetime
from pathlib import Path

from flask import Flask, abort, redirect, request, url_for
from slugify import slugify

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
QUEUE_DIR = SCRIPT_DIR / "queue"
PROCESSED_DIR = QUEUE_DIR / "processed"
REJECTED_DIR = QUEUE_DIR / "rejected"
ITEMS_DIR = REPO_ROOT / "src" / "content" / "items"

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

app = Flask(__name__)

# -----------------------------------------------------------------------------

def list_pending() -> list[Path]:
    if not QUEUE_DIR.exists():
        return []
    return sorted(p for p in QUEUE_DIR.glob("*.json") if p.is_file())


def count_today(dir_: Path) -> int:
    if not dir_.exists():
        return 0
    today = date.today().isoformat()
    return sum(
        1 for p in dir_.iterdir()
        if p.is_file() and datetime.fromtimestamp(p.stat().st_mtime).date().isoformat() == today
    )


def yaml_escape(s: str) -> str:
    """Escape a value for a double-quoted YAML scalar."""
    return s.replace("\\", "\\\\").replace("\"", "\\\"")


def to_markdown(data: dict) -> str:
    """Convert a reviewed item dict into a markdown file with frontmatter."""
    def fmt_list(items: list[str]) -> str:
        return "[" + ", ".join(f"\"{yaml_escape(x)}\"" for x in items) + "]"

    published = data["published_at"]
    if isinstance(published, str):
        # Accept both date and datetime isoformat
        published_str = published[:10]
    else:
        published_str = str(published)

    lines = [
        "---",
        f'title: "{yaml_escape(data["title"])}"',
        f'summary: "{yaml_escape(data["summary"])}"',
        f'why_it_matters: "{yaml_escape(data["why_it_matters"])}"',
        f'source_url: "{data["source_url"]}"',
        f'source_domain: "{data["source_domain"]}"',
        f"published_at: {published_str}",
        f"topics: {fmt_list(data.get('topics', []))}",
        f"municipalities: {fmt_list(data.get('municipalities', []))}",
        f'content_type: "{data["content_type"]}"',
    ]
    if data.get("entities"):
        lines.append(f"entities: {fmt_list(data['entities'])}")
    if data.get("fingerprint"):
        lines.append(f'fingerprint: "{yaml_escape(data["fingerprint"])}"')
    if data.get("legislative_status"):
        lines.append(f'legislative_status: "{yaml_escape(data["legislative_status"])}"')
    if data.get("classification"):
        lines.append(f'classification: "{data["classification"]}"')
    if data.get("risk_flags"):
        lines.append(f"risk_flags: {fmt_list(data['risk_flags'])}")
    lines.append(f"featured: {str(bool(data.get('featured', False))).lower()}")
    if data.get("relevance_score") is not None:
        lines.append(f"relevance_score: {int(data['relevance_score'])}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


# -----------------------------------------------------------------------------
# HTML (inline — no templates dir needed)
# -----------------------------------------------------------------------------

BASE_CSS = """
:root {
  --brick: #B22222; --charcoal: #1F1F1F; --bone: #FAF9F6; --slate: #4A4A4A;
  --gold: #D4AF37; --rule: #E5E0D8;
}
* { box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--bone); color: var(--charcoal); margin: 0; padding: 0; }
header { border-bottom: 1px solid var(--rule); padding: 12px 16px; background: #fff; position: sticky; top: 0; z-index: 10; }
header .title { font-weight: 800; color: var(--brick); font-size: 18px; }
header .counts { font-size: 13px; color: var(--slate); margin-top: 4px; }
main { padding: 16px; max-width: 720px; margin: 0 auto; }
.card { background: #fff; border: 1px solid var(--rule); padding: 16px; margin-bottom: 16px; border-radius: 4px; }
.muted { color: var(--slate); font-size: 13px; }
label { font-size: 13px; font-weight: 600; display: block; margin-top: 12px; margin-bottom: 4px; }
input[type=text], textarea, select { width: 100%; padding: 8px; font-size: 14px; border: 1px solid var(--rule); border-radius: 3px; background: #fff; font-family: inherit; }
textarea { min-height: 90px; resize: vertical; }
.pills { display: flex; flex-wrap: wrap; gap: 6px; }
.pill { display: inline-block; }
.pill label { display: inline-flex; align-items: center; gap: 4px; background: var(--bone); border: 1px solid var(--rule); padding: 6px 10px; border-radius: 999px; cursor: pointer; font-weight: 500; font-size: 13px; margin: 0; }
.pill input { margin: 0; }
.row { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 16px; }
button { padding: 10px 14px; border-radius: 3px; border: 1px solid var(--rule); background: #fff; cursor: pointer; font-weight: 600; font-size: 14px; font-family: inherit; }
button.primary { background: var(--brick); color: #fff; border-color: var(--brick); }
button.ghost { background: transparent; }
button.danger { background: #fff; color: var(--brick); border-color: var(--brick); }
a { color: var(--brick); }
.readonly { background: var(--bone); padding: 8px; border-radius: 3px; font-size: 13px; border: 1px solid var(--rule); }
.badge { display: inline-block; font-size: 11px; text-transform: uppercase; background: var(--brick); color: #fff; padding: 2px 6px; border-radius: 3px; margin-right: 6px; letter-spacing: 0.05em; }
.empty { text-align: center; padding: 60px 20px; color: var(--slate); }
@media (max-width: 500px) {
  main { padding: 12px; }
  .card { padding: 12px; }
}
"""


def page(body: str) -> str:
    pending = len(list_pending())
    approved = count_today(PROCESSED_DIR)
    rejected = count_today(REJECTED_DIR)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BRIC.News review</title>
<style>{BASE_CSS}</style>
</head>
<body>
<header>
  <div class="title">BRIC.News review queue</div>
  <div class="counts">
    Pending: <strong>{pending}</strong> ·
    Approved today: <strong>{approved}</strong> ·
    Rejected today: <strong>{rejected}</strong> ·
    <a href="/">Queue</a>
  </div>
</header>
<main>{body}</main>
</body>
</html>"""


def render_queue() -> str:
    items = list_pending()
    if not items:
        return page('<div class="empty">Queue is empty. Come back after the next ingest.</div>')
    rows = []
    for p in items:
        try:
            data = json.loads(p.read_text())
        except Exception:
            continue
        score = data.get("relevance_score", "?")
        ct = data.get("content_type", "?")
        title = data.get("title", p.stem)
        domain = data.get("source_domain", "")
        rows.append(
            f'<div class="card">'
            f'<div class="muted"><span class="badge">{ct}</span>score {score} · {domain}</div>'
            f'<h3 style="margin: 8px 0 4px 0">'
            f'<a href="/review/{p.name}" style="color: var(--charcoal); text-decoration: none;">{title}</a>'
            f'</h3>'
            f'<div class="muted">{p.name}</div>'
            f'</div>'
        )
    return page("\n".join(rows))


def render_review(p: Path, data: dict) -> str:
    def pills(name: str, options: list[str], selected: list[str]) -> str:
        out = ['<div class="pills">']
        for opt in options:
            checked = "checked" if opt in selected else ""
            out.append(
                f'<span class="pill"><label><input type="checkbox" name="{name}" value="{opt}" {checked}> {opt}</label></span>'
            )
        out.append("</div>")
        return "\n".join(out)

    ct = data.get("content_type", "news")
    ct_options = "\n".join(
        f'<option value="{c}" {"selected" if c == ct else ""}>{c}</option>' for c in CONTENT_TYPES
    )

    featured_checked = "checked" if data.get("featured") else ""
    title = data.get("title", "")
    source_url = data.get("source_url", "")
    summary = data.get("summary", "")
    why = data.get("why_it_matters", "")
    topics = data.get("topics", [])
    munis = data.get("municipalities", [])
    ro_score = data.get("relevance_score", "?")
    ro_flags = ", ".join(data.get("risk_flags", [])) or "none"
    ro_legis = data.get("legislative_status", "")
    ro_class = data.get("classification", "")

    body = f"""
<form method="post" action="/action/{p.name}">
  <div class="card">
    <div class="muted">
      <a href="{source_url}" target="_blank" rel="noopener noreferrer">{data.get("source_domain", "open source")}</a>
      · {str(data.get("published_at", ""))[:10]}
    </div>
    <h2 style="margin-top: 8px;">{title}</h2>

    <label>Summary</label>
    <textarea name="summary">{summary}</textarea>

    <label>Why it matters</label>
    <textarea name="why_it_matters">{why}</textarea>

    <label>Topics</label>
    {pills("topics", TOPICS, topics)}

    <label>Municipalities</label>
    {pills("municipalities", MUNICIPALITIES, munis)}

    <label>Content type</label>
    <select name="content_type">{ct_options}</select>

    <label style="margin-top: 16px;">
      <input type="checkbox" name="featured" value="1" {featured_checked}>
      Feature this item (appears in homepage Policy Watch for policy items, surfaces higher elsewhere)
    </label>

    <hr style="margin: 20px 0; border: 0; border-top: 1px solid var(--rule);">

    <label>Relevance score (from Haiku scorer, read-only)</label>
    <div class="readonly">{ro_score}</div>

    <label>Risk flags (read-only)</label>
    <div class="readonly">{ro_flags}</div>

    {f'<label>Legislative status (read-only)</label><div class="readonly">{ro_legis}</div>' if ro_legis else ''}
    {f'<label>Classification (read-only)</label><div class="readonly">{ro_class}</div>' if ro_class else ''}

    <div class="row">
      <button class="primary" name="action" value="approve" type="submit">Approve & publish</button>
      <button class="ghost" name="action" value="edit" type="submit">Save edits</button>
      <button class="danger" name="action" value="reject" type="submit">Reject</button>
      <a href="/" style="margin-left: auto; align-self: center;">Skip</a>
    </div>

    <details style="margin-top: 14px;">
      <summary class="muted" style="cursor: pointer;">Reject reason (required for reject)</summary>
      <label>Reason</label>
      <input type="text" name="reject_reason" placeholder="e.g. not Columbus-relevant">
    </details>
  </div>
</form>
"""
    return page(body)


# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------

@app.get("/")
def index():
    return render_queue()


@app.get("/review/<name>")
def review(name: str):
    p = QUEUE_DIR / name
    if not p.exists() or not p.is_file() or ".." in name:
        abort(404)
    data = json.loads(p.read_text())
    return render_review(p, data)


@app.post("/action/<name>")
def action(name: str):
    if ".." in name:
        abort(400)
    p = QUEUE_DIR / name
    if not p.exists():
        abort(404)
    data = json.loads(p.read_text())

    # Apply edits from form
    data["summary"] = request.form.get("summary", data.get("summary", "")).strip()
    data["why_it_matters"] = request.form.get("why_it_matters", data.get("why_it_matters", "")).strip()
    data["topics"] = request.form.getlist("topics")
    data["municipalities"] = request.form.getlist("municipalities")
    data["content_type"] = request.form.get("content_type", data.get("content_type", "news"))
    data["featured"] = bool(request.form.get("featured"))

    act = request.form.get("action", "edit")

    if act == "approve":
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        ITEMS_DIR.mkdir(parents=True, exist_ok=True)

        date_part = str(data.get("published_at", ""))[:10] or date.today().isoformat()
        slug = slugify(data.get("title", "item"))[:60] or "item"
        md_path = ITEMS_DIR / f"{date_part}-{slug}.md"
        counter = 1
        while md_path.exists():
            md_path = ITEMS_DIR / f"{date_part}-{slug}-{counter}.md"
            counter += 1
        md_path.write_text(to_markdown(data))
        shutil.move(str(p), PROCESSED_DIR / p.name)
        return redirect(url_for("index"))

    if act == "reject":
        REJECTED_DIR.mkdir(parents=True, exist_ok=True)
        reason = (request.form.get("reject_reason") or "no reason given").strip()
        sidecar = REJECTED_DIR / (p.stem + ".reason.json")
        sidecar.write_text(json.dumps({"reason": reason, "rejected_at": datetime.now().isoformat()}, indent=2))
        shutil.move(str(p), REJECTED_DIR / p.name)
        return redirect(url_for("index"))

    # Save edits in place
    p.write_text(json.dumps(data, indent=2))
    return redirect(url_for("review", name=name))


if __name__ == "__main__":
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    REJECTED_DIR.mkdir(parents=True, exist_ok=True)
    port = int(os.environ.get("BRIC_REVIEW_PORT", "4001"))
    app.run(host="127.0.0.1", port=port, debug=False)
