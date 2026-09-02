// Front-page ordering.
//
// The home page leads with the best RECENT story, not the best story ever.
// Ranking by relevance_score alone meant a high-scoring item from months ago
// held the lead slot forever and the site looked stale even while the daily
// feed was publishing normally.
//
// So: take everything published within RECENT_DAYS and rank that pool by
// (featured, relevance_score, date). Only if the pool is too small to fill
// the slots do we fall back to the full archive.
//
// To show a longer or shorter stretch of news on the front page, change
// RECENT_DAYS. To pin one story to the top regardless of age, set
// `featured: true` in that item's markdown, and remember to remove it later.

export const RECENT_DAYS = 45;

type ItemLike = {
  data: {
    featured?: boolean;
    relevance_score?: number | null;
    published_at: Date;
  };
};

/** featured first, then relevance score, then newest. */
export function byEditorialRank<T extends ItemLike>(a: T, b: T): number {
  const af = a.data.featured ?? false;
  const bf = b.data.featured ?? false;
  if (af !== bf) return af ? -1 : 1;
  const rs = (b.data.relevance_score ?? 0) - (a.data.relevance_score ?? 0);
  if (rs !== 0) return rs;
  return b.data.published_at.getTime() - a.data.published_at.getTime();
}

/**
 * Rank `items` for a front-page slot, preferring the last RECENT_DAYS.
 * `need` is how many items the caller intends to show; if fewer than that
 * are recent, older items are appended (still in editorial order) so the
 * layout never renders short.
 */
export function frontPageOrder<T extends ItemLike>(items: T[], need: number): T[] {
  const cutoff = Date.now() - RECENT_DAYS * 24 * 60 * 60 * 1000;
  const recent = items.filter((i) => i.data.published_at.getTime() >= cutoff);
  const older = items.filter((i) => i.data.published_at.getTime() < cutoff);
  if (recent.length >= need) return recent.sort(byEditorialRank);
  return [...recent.sort(byEditorialRank), ...older.sort(byEditorialRank)];
}
