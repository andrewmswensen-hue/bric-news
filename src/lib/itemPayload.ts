import type { CollectionEntry } from 'astro:content';
import { SCOPE_LABELS, PARTNERS } from '../content/config';

/**
 * Everything the pop-up detail view needs, serialized onto the card as a
 * data attribute so opening an item never needs a network request.
 */
export interface ItemPayload {
  slug: string;
  title: string;
  summary: string;
  detail: string;
  why: string;
  sourceUrl: string;
  sourceName: string;
  sourceDomain: string;
  date: string;       // ISO yyyy-mm-dd
  dateLabel: string;  // "Apr 15, 2026"
  scope: string;
  scopeLabel: string;
  contentType: string;
  typeLabel: string;
  topics: string[];
  municipalities: string[];
  partner: { name: string; url: string; cta: string } | null;
  realSource: boolean;
  sectionHref: string;
}

export const TYPE_LABELS: Record<string, string> = {
  policy: 'Policy',
  news: 'News',
  market_data: 'Market data',
  event: 'Event',
};

export function isRealSource(url: string | undefined): boolean {
  return !!url && !url.includes('example.com');
}

export function itemPayload(item: CollectionEntry<'items'>): ItemPayload {
  const d = item.data;
  const partner = d.partner ? PARTNERS[d.partner] : null;
  return {
    slug: item.slug,
    title: d.title,
    summary: d.summary,
    detail: d.detail || d.summary,
    why: d.why_it_matters,
    sourceUrl: d.source_url,
    sourceName: d.source_name || d.source_domain,
    sourceDomain: d.source_domain,
    date: d.published_at.toISOString().slice(0, 10),
    dateLabel: d.published_at.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' }),
    scope: d.scope,
    scopeLabel: SCOPE_LABELS[d.scope],
    contentType: d.content_type,
    typeLabel: TYPE_LABELS[d.content_type] ?? 'News',
    topics: d.topics,
    municipalities: d.municipalities,
    partner: partner ? { name: partner.name, url: partner.url, cta: partner.cta } : null,
    realSource: isRealSource(d.source_url),
    sectionHref: d.content_type === 'policy' ? '/policy' : '/news',
  };
}
