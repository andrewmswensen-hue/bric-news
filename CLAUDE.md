# BRIC.News — project guide

**What it is:** A Columbus, Ohio real estate investor news hub. A daily
syndication feed of local, Ohio, and national stories (each an original
summary plus a link to the publisher), a free vendor directory, guides, and a
resource hub. Positioned as an independent publication: no paid placements,
no sponsored news coverage, one disclosed editorial partner (RL Property
Management, see `/about#partners`).

**Tagline:** Buy. Rent. Invest. Columbus.

---

## Where things live (updated 2026-09-01)

| Thing | Status |
|---|---|
| GitHub repo | `andrewmswensen-hue/bric-news` (personal account, private). Transferred from the `bric-news` org on 2026-09-01. |
| Preview site | https://andrewmswensen-hue.github.io/bric-news/ — GitHub Pages, rebuilt on every push to `main` by `pages.yml`. The old Cloudflare deploy was removed on 2026-09-01; nothing Cloudflare remains. |
| Domain `bric.news` | Registered at Spaceship, still on their parking page. Not connected. |
| Daily feed | A scheduled Claude Code task (`bric-daily-feed`, every other day 7:30 AM) on the publisher's Mac follows `scripts/AGENT_RUN.md`. Runs on the Claude plan; **no API key anywhere.** Model pinned to Sonnet via `.claude/settings.json`. |
| Publish workflow | `.github/workflows/pages.yml`. Builds, prefixes links with `/bric-news/` (`scripts/pages-base.mjs`), deploys to Pages. |
| Newsletter | `scripts/newsletter.py` + manual-only workflow. Beehiiv not connected. Signup form on the site is still a placeholder. |

To build and preview locally:

```bash
npm install && npm run build && npm run preview
```

---

## How the daily feed works

`scripts/supplemental_ingest.py` reads `scripts/supplemental_sources.yaml`:

1. **Collect.** Pull every RSS feed and Google News query. Drop entries older
   than 3 days, blocklisted or foreign domains, kill-pattern titles
   (listicles, opinion, podcasts, daily rate tickers, roundups), and anything
   seen before. Google News links are resolved to the real publisher URL.
2. **Score.** Haiku (`claude-haiku-4-5`) scores each candidate 1-10 against
   its tier's rubric using only the headline and feed snippet. It can also
   hard-kill (opinion, marketing, no property nexus, daily noise).
3. **Select.** Per tier, keep items at or above the bar, rank by score, fill
   the daily cap. National: bar 8, cap 2. State: bar 7, cap 3. Local: bar 5,
   cap 10. Partner (RLPM) items: bar 6.
4. **Write.** Honor robots.txt, fetch the article, skip paywalls and thin
   pages, have Sonnet (`claude-sonnet-5`) write the BRIC title / summary /
   detail / why-it-matters, reject generic why-it-matters lines, dedupe by
   fingerprint, write markdown into `src/content/items/`.

Either runner then opens a **pull request** (`feed/YYYY-MM-DD`) with every
item summarized in the PR body. Merge it to publish. After
`settings.review_required_until` (2026-10-02) it commits to main directly.
`scripts/state.json` is the dedupe ledger and is committed on every run.

**How it runs.** `scripts/AGENT_RUN.md` breaks the pipeline into
`--collect-json`, `--fetch`, and `--write-items` so the Claude session does
the scoring and writing itself. Caps, kill patterns, generic-why rejection,
dedupe, robots, and paywall checks live in the script. The script's
unattended API mode (Haiku + Sonnet via `ANTHROPIC_API_KEY`) still exists
for local use but nothing schedules it.

Test without spending anything:

```bash
.venv/bin/python scripts/supplemental_ingest.py --dry-run
```

`scripts/review.py` (the old local Flask review tool) is superseded by the
PR flow and is no longer used by anything.

---

## Repo layout

- `src/pages/` — routes: home, `/news`, `/policy`, `/resources`, `/guides`,
  `/business-directory`, `/about`, per-city `/[city]`, plus `rss.xml` and
  `sitemap.xml` endpoints.
- `src/components/` — Astro components. `ItemDetail.astro` is the pop-up
  detail view (included once in `BaseLayout`); every item card carries its
  payload in `data-item` and opens it. `ItemRow.astro` is the compact row
  for "More from the feed."
- `src/lib/itemPayload.ts` — builds that payload; single source of truth for
  labels (scope, type, partner).
- `src/content/` — Markdown content validated by `src/content/config.ts`:
  `items/`, `cities/`, `resources/`, `vendors/`, `guides/`.
- `scripts/` — the Python pipeline (above) and `newsletter.py`.
- `public/` — logos, favicon, `robots.txt`, `llms.txt`.

### Item schema notes

Beyond the original fields, items now carry `scope` (local / regional /
state / national; defaults to local), `detail` (longer text for the pop-up),
`source_name`, optional `partner: rlpm`, and an audit trail (`ingested_at`,
`ingest_model`, `ingest_prompt_version`, `ingest_source_id`). Six of the
original 36 items are placeholders with `example.com` source URLs; the site
shows "Source link pending" for those.

---

## Stack

Astro 4 (static) · Tailwind · Markdown content · Python 3.12 pipeline ·
Anthropic API (Haiku scores, Sonnet writes) · GitHub Actions · GitHub Pages · Beehiiv (later).

## Brand

Colors: brick `#972E28` (CSS var; `#B22222` in older docs) · charcoal
`#1F1F1F` · bone `#FAF9F6` · slate `#4A4A4A` · gold `#D4AF37` (used for the
Partner label) · rule `#E5E0D8`. Type: Montserrat headings, Inter body.

Voice: newswire summary plus a plain "why it matters." No em dashes, no AI
buzzwords, no absolutes, no legal or investment advice, no property
management company named in items unless it is the news.

---

## Open items, in priority order

1. Keep merging the feed PRs; flip `review_required_until` in
   `scripts/supplemental_sources.yaml` to auto-publish when ready.
2. Connect `bric.news` when ready: CNAME at Spaceship → `andrewmswensen-hue.github.io`,
   custom domain in Pages settings, drop the base-path step in `pages.yml`.
3. Replace the placeholder Beehiiv form; connect the newsletter workflow.
4. Ask Columbus Underground and Columbus Business First for feed permission
   (both 403 automated access; they are blocklisted until then).
5. Hand-verify replacement feed URLs for HUD, FHFA, WOSU, the Dispatch.
6. UI refresh pass once real feed content is flowing.
