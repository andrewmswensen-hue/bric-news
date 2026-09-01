# BRIC.News daily feed — procedure for a Claude Code session

This is the procedure a scheduled Claude Code task follows to run the daily
feed on the publisher's Claude plan (no API key). The script does every
mechanical and rule-enforcing step; the session does the two judgment steps:
scoring and writing. Follow it in order and do not skip the gates.

Repo: `~/Documents/Claude/Projects/BRIC.news` (branch `main`).
Python: `.venv/bin/python` inside the repo. If `.venv` is missing, create it:
`python3 -m venv .venv && .venv/bin/pip install -r scripts/requirements.txt`.

## 1. Sync

```bash
git checkout main && git pull --ff-only origin main
```

If the pull fails, stop and report; do not force anything.

## 2. Collect (no AI)

```bash
.venv/bin/python scripts/supplemental_ingest.py --collect-json /tmp/bric-candidates.json
```

Read `/tmp/bric-candidates.json`. It contains:

- `tiers` — for each tier: `min_score`, `daily_cap`, `open_slots` (how many
  items that tier may still publish today), and the full scoring `rubric`.
- `scorer_instructions` and `writer_instructions` — the house rules. Read
  them once; they are the same rules the API path uses.
- `candidates` — every fresh item that survived the mechanical filters, each
  with `id`, `tier`, `min_score`, `title`, `url`, `publisher_name`,
  `snippet`, `partner`, and (for partner items) `feed_body`.

If a tier has `open_slots: 0`, skip that tier entirely. If there are no
candidates at all, stop and report "nothing new today."

## 3. Score (judgment step 1)

For every candidate, decide a `relevance_score` 1-10 against its tier's
rubric, using only the title and snippet. Apply the scorer instructions
literally: score conservatively, and mark `kill: true` for opinion, listicles,
marketing, daily rate ticks, or anything without a housing / property /
landlord nexus. Do this in one pass; do not fetch articles yet.

Then, per tier, take the highest-scoring candidates that meet `min_score`,
up to `open_slots`. Keep two or three runners-up per tier in case a selected
item fails the fetch.

## 4. Fetch and write (judgment step 2)

For each selected candidate:

```bash
.venv/bin/python scripts/supplemental_ingest.py --fetch "<url>"
```

The output is JSON. If `ok` is false (robots.txt, paywall, thin page, fetch
failure), drop the item and move to a runner-up. Never work around a
paywall or a robots refusal. Partner items can use `feed_body` from the
candidates file instead of fetching.

With the body in hand, write the item exactly per `writer_instructions`:
`title`, `summary` (≤100 words), `detail` (150-250 words; 250-350 for
partner), `why_it_matters` (specific, never generic), `why_specificity`
(honest 1-5), `topics`, `municipalities` (local tier only), `content_type`,
`entities`, `source_name`, `risk_flags`. No em dashes, no buzzwords, no
absolutes, no advice, no property-management company named unless it is
the news.

Collect the written records into one JSON array at `/tmp/bric-items.json`.
Each record must also carry these fields copied from the candidate:
`tier`, `min_score`, `relevance_score`, `url`, `source_domain`,
`publisher_name`, `published_at`, `source_id`, `partner`.

## 5. Gate and write files (no AI)

```bash
.venv/bin/python scripts/supplemental_ingest.py --write-items /tmp/bric-items.json --mark-seen /tmp/bric-candidates.json --model-name "<the model you are running as>"
```

This applies the caps, rejects generic why-it-matters lines, dedupes by
fingerprint, writes the markdown files into `src/content/items/`, updates
`scripts/state.json`, and writes `scripts/last_run.md` (the human-readable
report). Read the log lines; anything marked `[why-generic]` or `[low]` was
rejected on purpose. Do not rewrite a rejected item to sneak it through.

## 6. Publish

Read `scripts/last_run.json` for `mode` and the count.

- **If `mode` is `review`** (before 2026-10-02): push a branch and open a
  pull request so the publisher can read the batch before it goes live.

  ```bash
  d=$(date +%F)
  git checkout -B "feed/$d"
  git add src/content/items scripts/state.json
  git commit -m "Daily feed: $d ($(jq '.published|length' scripts/last_run.json) items)"
  git push -f origin "feed/$d"
  gh pr create --base main --head "feed/$d" --title "Daily feed: $d" --body-file scripts/last_run.md || gh pr edit "feed/$d" --body-file scripts/last_run.md
  git checkout main
  ```

- **If `mode` is `auto`**: commit to main directly.

  ```bash
  git add src/content/items scripts/state.json
  git commit -m "Daily feed: $(date +%F) ($(jq '.published|length' scripts/last_run.json) items)"
  git pull --rebase origin main && git push origin main
  ```

GitHub Pages rebuilds the preview site automatically on push to `main`.

If nothing was published, still commit `scripts/state.json` to main so the
next run does not re-score the same headlines.

## 7. Report

End with a short plain-English summary: how many candidates, how many
published per tier, anything rejected at a gate, and the PR link if one was
opened. Keep it to a few lines.

## Rules that are not negotiable

- Never bypass a paywall or robots.txt. Skip the item.
- Never publish an item without a working `url` and a named publisher.
- Never exceed a tier's `open_slots`; the script enforces it, do not fight it.
- Never edit `scripts/supplemental_sources.yaml` or the prompts during a run.
- If anything looks wrong (feeds all failing, git conflicts, an empty
  candidates file two days running), stop and report instead of improvising.
