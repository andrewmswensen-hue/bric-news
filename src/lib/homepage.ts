import type { CollectionEntry } from 'astro:content';
import { frontPageOrder } from './frontPage';

/**
 * Picks every item the home page shows, in one pass.
 *
 * The home page is organized by PLACE, not by type. A Columbus landlord reads
 * "what happened here" first, then "what is coming at me from the statehouse
 * and Washington." Type (policy / news / market data / event) is a badge on
 * the card, not a section heading.
 *
 * That matters because each band is defined by the same `scope` field the
 * badge reads from, so a heading cannot drift out of sync with its contents.
 * The old layout split by content_type only, which meant national and Ohio
 * items landed under headings that promised Columbus coverage.
 *
 * Every band draws from one shared pool and marks what it took, so no item
 * can appear twice on the page.
 */

type Item = CollectionEntry<'items'>;

const COLUMBUS_SCOPES = ['local', 'regional'];
const AWAY_SCOPES = ['state', 'national'];

export interface HomepagePlan {
  /** Hero: the lead policy story and the lead non-policy story, any scope. */
  heroPolicy: Item | null;
  heroNews: Item | null;
  /** Ordinances, permits, and enforcement in the 13 tracked jurisdictions. */
  columbusPolicy: Item[];
  /** Local development, market data, and events. */
  columbusNews: Item[];
  /** Statehouse and federal items, any type. */
  ohioNational: Item[];
}

export function planHomepage(all: Item[]): HomepagePlan {
  const used = new Set<string>();

  /** Take up to `n` items matching `where`, skipping anything already placed. */
  const take = (where: (i: Item) => boolean, n: number): Item[] => {
    const pool = all.filter((i) => !used.has(i.slug) && where(i));
    const picked = frontPageOrder(pool, n).slice(0, n);
    picked.forEach((i) => used.add(i.slug));
    return picked;
  };

  const isColumbus = (i: Item) => COLUMBUS_SCOPES.includes(i.data.scope);
  const isAway = (i: Item) => AWAY_SCOPES.includes(i.data.scope);
  const isPolicy = (i: Item) => i.data.content_type === 'policy';
  // Partner content never takes a hero slot; see /about#partners.
  const notPartner = (i: Item) => !i.data.partner;

  // Hero first, so the strongest stories lead the page and the bands below
  // fill in around them rather than repeating them.
  const heroPolicy = take((i) => isPolicy(i) && notPartner(i), 1)[0] ?? null;
  const heroNews = take((i) => !isPolicy(i) && notPartner(i), 1)[0] ?? null;

  return {
    heroPolicy,
    heroNews,
    columbusPolicy: take((i) => isColumbus(i) && isPolicy(i), 3),
    columbusNews: take((i) => isColumbus(i) && !isPolicy(i), 3),
    ohioNational: take(isAway, 3),
  };
}
