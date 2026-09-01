import rss from '@astrojs/rss';
import { getCollection } from 'astro:content';
import type { APIContext } from 'astro';
import { isRealSource } from '../lib/itemPayload';

/**
 * BRIC's own feed. Each entry links back to BRIC (the item opens in the
 * pop-up view via #item=slug) and credits the original publisher in the
 * description, so downstream readers see the attribution too.
 */
export async function GET(context: APIContext) {
  const site = context.site?.toString().replace(/\/$/, '') ?? 'https://bric.news';
  const items = (await getCollection('items'))
    .filter((i) => isRealSource(i.data.source_url))
    .sort((a, b) => b.data.published_at.getTime() - a.data.published_at.getTime())
    .slice(0, 50);

  return rss({
    title: 'BRIC.News',
    description: 'Columbus, Ohio real estate investor news: local policy, market data, and the national and state rules that reach Columbus landlords. Buy. Rent. Invest. Columbus.',
    site,
    customData: '<language>en-us</language>',
    items: items.map((i) => {
      const d = i.data;
      const section = d.content_type === 'policy' ? '/policy' : '/news';
      const publisher = d.source_name || d.source_domain;
      return {
        title: d.title,
        pubDate: d.published_at,
        link: `${site}${section}#item=${encodeURIComponent(i.slug)}`,
        description: `${d.summary} Why it matters: ${d.why_it_matters} (Source: ${publisher}, ${d.source_url})`,
        categories: [...d.topics, d.scope],
      };
    }),
  });
}
