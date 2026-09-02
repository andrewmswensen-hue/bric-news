# BRIC.News — v1

> **Repo:** https://github.com/andrewmswensen-hue/bric-news · **Preview:** https://andrewmswensen-hue.github.io/bric-news/
>
> Everything lives on GitHub until launch: code, content, the daily feed, and the preview site (GitHub Pages, rebuilt on every push to `main`). See `CLAUDE.md` for current status.

**BRIC.News** is a continuously updated Columbus, Ohio real estate investor resource hub. It publishes local policy updates, market data, development news, a curated vendor directory, and a resource hub pulling together the municipal, county, and legal references investors reach for repeatedly.

BRIC.News is positioned as an independent publication. It takes no sponsorships for news coverage, lists vendors for free, and uses editorial "Preferred" designations (one per category) rather than paid placements.

---

## What's in this repo

- **`src/`** — the Astro site (homepage, section pages, per-city hubs, vendor directory, resource hub, about page).
- **`src/content/`** — Markdown collections (items, vendors, cities, resources) and their schemas.
- **`scripts/`** — Python pipeline: tiered RSS/Google News ingest (`supplemental_ingest.py` + `supplemental_sources.yaml`), weekly Beehiiv draft builder. `review.py` is the retired local review tool.
- **`.github/workflows/`** — GitHub Pages preview publish, manual newsletter draft. (The feed itself runs from a scheduled Claude task on the publisher's Mac; see `scripts/AGENT_RUN.md`.)

---

## Architecture

| Layer | Tool |
|---|---|
| Framework | Astro 4 (static output) |
| Styling | Tailwind CSS |
| Hosting | GitHub Pages (preview until launch) |
| Source control | GitHub |
| Content | Markdown in `src/content/` |
| Ingest | Python 3.11+ |
| AI | Anthropic Claude API (`claude-haiku-4-5` scores relevance, `claude-sonnet-5` writes summaries) |
| Newsletter | Beehiiv (draft creation via API) |
| Analytics | none yet |

Data flow:

```
DAILY (or every other day)
  A scheduled Claude Code task on the publisher's Mac follows scripts/AGENT_RUN.md:
    1. collect   scripts/supplemental_ingest.py --collect-json   (feeds + filters, no AI)
    2. score     the Claude session rates each headline against its tier rubric
    3. fetch     scripts/supplemental_ingest.py --fetch <url>    (robots, paywall, thin-page checks)
    4. write     the session writes title / summary / detail / why-it-matters
    5. gate      scripts/supplemental_ingest.py --write-items    (caps, dedupe, style rules -> markdown)
  -> before 2026-10-02: pull request "Daily feed: <date>" for review (merge = publish)
  -> after:             commits straight to main
  -> GitHub Pages rebuilds the preview on every push to main

MONDAY (separate Claude skill, not in this repo)
  Crane policy email -> item-registry.json -> policy items

FRIDAY (manual for now)
  scripts/newsletter.py -> Beehiiv draft
```

The BRIC publish skill (Monday-pipeline → BRIC queue) is **not** in this repo. It's a separate Claude skill Andrew configures alongside the existing Monday pipeline.

---

## Local dev setup

**Prerequisites:** Node 20+, Python 3.11+, an Anthropic API key (for the feed), a Beehiiv publication (for the newsletter, later).

### 1. Clone and install

```bash
git clone https://github.com/andrewmswensen-hue/bric-news.git
cd bric-news
npm install
```

### 2. Copy env file

```bash
cp .env.example .env
# Fill in ANTHROPIC_API_KEY, BEEHIIV_API_KEY, BEEHIIV_PUBLICATION_ID
```

### 3. Run the site

```bash
npm run dev
```

Opens at `http://localhost:4321`.

### 4. Build for production

```bash
npm run build      # writes to dist/
npm run preview    # serve dist/ locally
```

---

## Pipeline setup (Python)

### 1. Create a virtual env and install deps

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r scripts/requirements.txt
```

### 2. Dry-run the ingest (no API calls, no writes)

```bash
python scripts/supplemental_ingest.py --dry-run
```

This parses `scripts/supplemental_sources.yaml`, fetches every feed and Google News query, resolves links, applies every filter that does not need AI, and prints the candidate list per tier. No API calls, nothing written. Useful for sanity checking before spending Anthropic credits.

### 3. Run a real ingest (one-off)

```bash
source .venv/bin/activate
export ANTHROPIC_API_KEY=sk-ant-...
python scripts/supplemental_ingest.py --limit 10
```

New items are written directly to `src/content/items/`, and a run report lands in `scripts/last_run.md`. In the GitHub workflow those files go into a pull request for review; locally, look at the files and commit what you want.

---

## Reviewing the daily feed

Every morning the workflow opens a pull request named `Daily feed: YYYY-MM-DD (N items)`. The PR description lists every item with its tier, score, source, summary, and any flags the writer raised. Read it in the GitHub app or on the web:

- **Publish everything:** merge the PR.
- **Drop one item:** delete its file from the PR (GitHub's file view has a delete button), then merge.
- **Skip the day:** close the PR. The items are remembered as seen and will not come back.

After `review_required_until` in `supplemental_sources.yaml` (2026-10-02) the workflow commits to `main` directly and no PR is opened.

`scripts/review.py`, the old local Flask review tool, still exists but nothing uses it anymore.

## Newsletter

```bash
source .venv/bin/activate
export ANTHROPIC_API_KEY=...
export BEEHIIV_API_KEY=...
export BEEHIIV_PUBLICATION_ID=...
python scripts/newsletter.py
```

Pulls items from the last 7 days that are either `featured: true` or `relevance_score >= 7`, groups them into policy / news / events sections, adds a vendor spotlight and a featured resource (rotating weekly by ISO week), and creates a **draft** (not sent) in Beehiiv. Prints the draft URL on success.

Without Beehiiv credentials, the script writes `scripts/newsletter-preview.html` for local review.

---

## Deployment

Until launch the site is published to **GitHub Pages** by `.github/workflows/pages.yml` on every push to `main`:

https://andrewmswensen-hue.github.io/bric-news/

GitHub Pages serves project sites under `/bric-news/`, so the workflow runs `scripts/pages-base.mjs` after the build to prefix every root-relative link. Nothing in `src/` knows about the prefix; when the site moves to `bric.news`, delete that one step.

Required repository secrets (GitHub → Settings → Secrets and variables → Actions):

- `BEEHIIV_API_KEY` and `BEEHIIV_PUBLICATION_ID` — for the newsletter, once Beehiiv is connected.

### Going live on `bric.news` (later)

The domain is registered at Spaceship and currently parked. GitHub Pages supports custom domains: add a `CNAME` record at Spaceship pointing `bric.news` at `andrewmswensen-hue.github.io`, set the custom domain in the repo's Pages settings, and remove the base-path step from `pages.yml`.

## Weekly operational flow

| Day | Time | What happens | Your action |
|---|---|---|---|
| Every day (or every other) | morning | Scheduled Claude task runs `scripts/AGENT_RUN.md` and opens a PR with the day's items (roughly 5-15) | Read the PR, merge (~5 min) |
| Monday | 9 AM | Crane email → Monday skill → policy items via the registry | Review alongside the daily PR |
| Friday | manual | Newsletter draft (once Beehiiv is connected) | Review & send (~15 min) |
| Any time | — | Manual vendor/resource/city additions via markdown edits | Commit & push |

Missing a day is fine. The pipeline tolerates skipped reviews; items queue up.

---

## Adding content manually

Bypassing the pipeline is the normal path for vendors, resources, cities, and occasional hand-curated items.

### Add a vendor

Create a markdown file at `src/content/business-directory/<slug>.md`:

```markdown
---
name: "Vendor Name"
category: "maintenance-repair"
sub_categories: ["plumbing", "drain"]
service_area: ["columbus", "franklin-county"]
website: "https://example.com"
phone: "614-555-0100"
description: "Short 2-3 sentence description."
licensed: true
preferred: false
last_verified: 2026-04-15
consent_status: "public_listing"
---
```

Commit and push. The site rebuilds automatically.

### Add a resource

Create `src/content/resources/<slug>.md`:

```markdown
---
label: "Franklin County Auditor Lookup"
url: "https://property.franklincountyauditor.com"
category: "government"
municipalities: ["franklin-county"]
description: "One-sentence description."
---
```

### Add a city

Create `src/content/cities/<municipality-slug>.md`. The slug must match one of the allowed values in `src/content/config.ts` (`MUNICIPALITIES` enum).

---

## Troubleshooting

**`npm run build` fails with content schema error.**
Run `npm run astro sync` or delete `.astro/` and try again. Most often this is a Zod validation issue — check the referenced markdown file's frontmatter against `src/content/config.ts`.

**Ingest script says `ANTHROPIC_API_KEY missing`.**
Either `source .venv/bin/activate && export ANTHROPIC_API_KEY=...` or put the key in `.env` and use a tool like `dotenv-cli` or `direnv`. Alternatively run with `--dry-run` to test without any API call.

**The scheduled feed task did not run.**
It only runs while the Claude desktop app is open; if the Mac was asleep or the app closed at 7:30 AM it runs on next launch. Check the Scheduled section in the app's sidebar.

**The daily feed opened a PR with nothing local in it.**
Local sources are Google News queries plus the RLPM feed; on a quiet day they can all fall below the bar. Check the "Run stats" section of the PR body: `below_bar` and `killed` counts tell you whether items were found and rejected or never found at all.

**Build fails with "Cannot find module '@astrojs/tailwind'"**
Run `npm ci` locally, commit the updated `package-lock.json`, push.

**Beehiiv draft API returns 401.**
Double-check `BEEHIIV_API_KEY` is a **Publication API Key** (not an account token). Generate from Beehiiv → Settings → Integrations → API.

---

## Decisions made during initial build

- **Cloudflare dropped (Sep 2026).** The original build deployed to a personal Cloudflare account. That was removed; the site publishes to GitHub Pages until launch.
- **Beehiiv embed is stubbed.** The homepage and `<NewsletterCTA />` component use a placeholder form posting to `subscribe.example.beehiiv.com`. When the Beehiiv publication is live, replace the two `data-beehiiv-placeholder` forms in `src/components/NewsletterCTA.astro` with the real embed code from Beehiiv → Website → Forms → Embed. The newsletter-drafting backend (`scripts/newsletter.py`) is already wired to the Beehiiv API and expects `BEEHIIV_API_KEY` and `BEEHIIV_PUBLICATION_ID`.
- **`@astrojs/sitemap` was removed.** The plugin conflicted with a build-hook change in Astro 4.15. Sitemap generation can be re-added later (or generated from a custom Astro endpoint) — not launch-blocking.
- **Sitemap and RSS added Sep 2026** at `/sitemap.xml` and `/rss.xml`.

---

## Open TODOs (carry into post-launch polish)

- Verify real RSS URLs for Central Ohio REIA, BIA Central Ohio, Franklin County Auditor, and Columbus Business First (placeholders marked in `scripts/supplemental_sources.yaml`).
- Replace smaller-city resource URLs (Reynoldsburg, Licking County, Worthington, etc.) with verified current links where placeholder patterns were used.
- Wire a real notification destination for the daily-ingest workflow (email, Slack webhook, or push — currently just logs).
- Replace the placeholder Beehiiv embed in `src/components/NewsletterCTA.astro` once the publication is live.
- Set up Cloudflare Web Analytics and paste the tracking snippet into `src/layouts/BaseLayout.astro`.

---

## Brand quick reference

- **Name:** BRIC.News
- **Tagline:** Buy. Rent. Invest. Columbus.
- **Positioning:** Columbus investor news, vendors, and resources. Updated daily.
- **Colors:** brick `#B22222` · charcoal `#1F1F1F` · bone `#FAF9F6` · slate `#4A4A4A` · gold `#D4AF37` · rule `#E5E0D8`
- **Type:** Montserrat (headings), Inter (body), system mono.
- **Voice:** newswire summary + educational "why it matters". No em dashes. No AI buzzwords. No absolutes. No RLPM mentions in items.
