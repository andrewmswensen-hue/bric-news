# BRIC.News — v1

> **Live preview:** https://bric-news-site.andrew-m-swensen.workers.dev (deployed via Cloudflare Workers Static Assets, auto-deploys on push to `main`).

**BRIC.News** is a continuously updated Columbus, Ohio real estate investor resource hub. It publishes local policy updates, market data, development news, a curated vendor directory, and a resource hub pulling together the municipal, county, and legal references investors reach for repeatedly.

BRIC.News is positioned as an independent publication. It takes no sponsorships for news coverage, lists vendors for free, and uses editorial "Preferred" designations (one per category) rather than paid placements.

---

## What's in this repo

- **`src/`** — the Astro site (homepage, section pages, per-city hubs, vendor directory, resource hub, about page).
- **`src/content/`** — Markdown collections (items, vendors, cities, resources) and their schemas.
- **`scripts/`** — Python pipeline: supplemental RSS/Google News ingest, Flask review tool, weekly Beehiiv draft builder.
- **`.github/workflows/`** — Cloudflare Pages deploy, daily ingest, weekly newsletter draft.

---

## Architecture

| Layer | Tool |
|---|---|
| Framework | Astro 4 (static output) |
| Styling | Tailwind CSS |
| Hosting | Cloudflare Pages |
| Source control | GitHub |
| Content | Markdown in `src/content/` |
| Ingest | Python 3.11+ |
| AI | Anthropic Claude API (`claude-haiku-4-5-20251001` for scoring, `claude-opus-4-7` for rewrites and intros) |
| Newsletter | Beehiiv (draft creation via API) |
| Analytics | Cloudflare Web Analytics |

Data flow:

```
MONDAY MORNING
  Crane email → Monday skill → BRIC publish skill (separate)
  → writes JSON to scripts/queue/
  → review.py approves → markdown in src/content/items/
  → git push → Cloudflare deploy

TUESDAY–FRIDAY 7 AM ET
  GitHub Action runs scripts/supplemental_ingest.py
  → Haiku scores relevance, Opus rewrites ≥5 items
  → writes JSON to scripts/queue/ on branch queue-review/YYYY-MM-DD
  → phone review via review.py → merge → Cloudflare deploy

FRIDAY 7 AM ET
  GitHub Action runs scripts/newsletter.py
  → pulls items from last 7 days (featured OR score ≥ 7)
  → creates a Beehiiv draft, prints URL
```

The BRIC publish skill (Monday-pipeline → BRIC queue) is **not** in this repo. It's a separate Claude skill Andrew configures alongside the existing Monday pipeline.

---

## Local dev setup

**Prerequisites:** Node 20+, Python 3.11+, a Cloudflare account, a GitHub repo, an Anthropic API key, a Beehiiv publication.

### 1. Clone and install

```bash
git clone https://github.com/<your-github-username>/bric-news.git
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

This parses `scripts/supplemental_sources.yaml`, fetches all RSS and Google News feeds, and reports how many new-vs-dupe items it would have processed. Useful for sanity checking before spending Anthropic credits.

### 3. Run a real ingest (one-off)

```bash
source .venv/bin/activate
export ANTHROPIC_API_KEY=sk-ant-...
python scripts/supplemental_ingest.py --limit 10
```

New items land in `scripts/queue/YYYY-MM-DD-slug.json`. Nothing publishes until you approve them in the review tool.

---

## Review tool

After an ingest runs, the queue contains JSON files awaiting your review:

```bash
source .venv/bin/activate
python scripts/review.py
```

Open `http://localhost:4001` on your laptop or phone. Each pending item shows:

- **Editable**: summary, why-it-matters, topics, municipalities, content_type, featured flag.
- **Read-only**: relevance score, risk flags, legislative status, classification.
- **Actions**: Approve & publish (writes markdown to `src/content/items/`, moves JSON to `scripts/queue/processed/`), Save edits (keeps it in queue), Reject (asks for reason, moves to `scripts/queue/rejected/`), Skip.

The header shows Pending / Approved today / Rejected today counts.

The tool runs on `localhost` only — no auth, not for public exposure.

---

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

## Deployment (Cloudflare Pages)

### First-time setup

1. **Create a Cloudflare account** (free) and sign in.
2. In the Cloudflare dashboard → **Pages** → **Create a project** → **Connect to Git**.
3. Authorize GitHub and select your `bric-news` repo.
4. Framework preset: **Astro**. Build command: `npm run build`. Build output: `dist`. Environment variable `NODE_VERSION=20`.
5. Save and deploy. You'll get a `bric-news.pages.dev` URL within a minute.

### Wire up the GitHub Action (optional but recommended)

The Cloudflare dashboard build is automatic on push. If you want the GitHub Action in `.github/workflows/deploy.yml` to also handle deploys, set these secrets in the repo's GitHub → Settings → Secrets and variables → Actions:

- `CLOUDFLARE_API_TOKEN` — create via **My Profile → API Tokens → Create Token → Edit Cloudflare Workers** template (gives you Pages deploy rights).
- `CLOUDFLARE_ACCOUNT_ID` — visible in the right sidebar of any Cloudflare dashboard page.

Set these other secrets for the pipeline workflows:

- `ANTHROPIC_API_KEY`
- `BEEHIIV_API_KEY`
- `BEEHIIV_PUBLICATION_ID`

### DNS configuration for `bric.news` (Spaceship)

Once the site is live on `bric-news.pages.dev`:

1. In Cloudflare Pages → your project → **Custom domains** → **Set up a custom domain** → enter `bric.news`.
2. Cloudflare will show you the DNS records to create.
3. In Spaceship (your registrar) → DNS settings for `bric.news`:
   - Add a **CNAME** record: `@` → `bric-news.pages.dev` (or whatever Cloudflare shows). Some registrars require CNAME flattening; Spaceship supports it.
   - Optionally, add a **CNAME** record: `www` → `bric-news.pages.dev`.
4. Wait 5–60 minutes for DNS propagation. Cloudflare will auto-provision an SSL cert.

---

## Weekly operational flow

| Day | Time | What happens | Your action |
|---|---|---|---|
| Monday | 9 AM | Crane email arrives → Monday skill → BRIC publish skill fills `scripts/queue/` with ~15–25 policy items | Review approved batch in `review.py` (~30 min) |
| Tue–Fri | 7 AM | Daily ingest action fills queue with ~2–5 non-policy items | Review queue from phone (~5 min) |
| Friday | 7 AM | Newsletter action creates a Beehiiv draft | Review & send (~15 min) |
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

**Flask review tool shows empty queue even after ingest.**
The queue lives in `scripts/queue/`. If items are in `scripts/queue/processed/` or `scripts/queue/rejected/`, they are not pending. If you ran the ingest on GitHub Actions, pull the `queue-review/YYYY-MM-DD` branch locally first.

**Cloudflare Pages build fails with "Cannot find module '@astrojs/tailwind'"**
Run `npm ci` locally, commit the updated `package-lock.json`, push.

**Beehiiv draft API returns 401.**
Double-check `BEEHIIV_API_KEY` is a **Publication API Key** (not an account token). Generate from Beehiiv → Settings → Integrations → API.

---

## Decisions made during initial build

- **GitHub repo URL** was not provided at build time. Placeholders using `<your-github-username>/bric-news` are used in docs; update the git remote when the real repo is created.
- **Cloudflare Pages subdomain first.** The site is ready to deploy to `bric-news.pages.dev` out of the box. DNS for `bric.news` → Cloudflare Pages is documented above but not wired.
- **Beehiiv embed is stubbed.** The homepage and `<NewsletterCTA />` component use a placeholder form posting to `subscribe.example.beehiiv.com`. When the Beehiiv publication is live, replace the two `data-beehiiv-placeholder` forms in `src/components/NewsletterCTA.astro` with the real embed code from Beehiiv → Website → Forms → Embed. The newsletter-drafting backend (`scripts/newsletter.py`) is already wired to the Beehiiv API and expects `BEEHIIV_API_KEY` and `BEEHIIV_PUBLICATION_ID`.
- **`@astrojs/sitemap` was removed.** The plugin conflicted with a build-hook change in Astro 4.15. Sitemap generation can be re-added later (or generated from a custom Astro endpoint) — not launch-blocking.
- **No sitemap, no RSS yet.** Both are easy to add pre-launch via `@astrojs/rss` and a hand-rolled sitemap endpoint if/when SEO becomes a priority.

---

## Open TODOs (carry into post-launch polish)

- Verify real RSS URLs for Central Ohio REIA, BIA Central Ohio, Franklin County Auditor, and Columbus Business First (placeholders marked in `scripts/supplemental_sources.yaml`).
- Replace smaller-city resource URLs (Reynoldsburg, Licking County, Worthington, etc.) with verified current links where placeholder patterns were used.
- Wire a real notification destination for the daily-ingest workflow (email, Slack webhook, or push — currently just logs).
- Add `@astrojs/rss` for a site-level RSS feed before public announcement.
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
