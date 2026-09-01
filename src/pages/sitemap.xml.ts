import { getCollection } from 'astro:content';
import type { APIContext } from 'astro';

/**
 * Hand-rolled sitemap. Section pages carry the date of their newest item so
 * crawlers know when the feed last moved; guides carry their own dates.
 */
export async function GET(context: APIContext) {
  const site = context.site?.toString().replace(/\/$/, '') ?? 'https://bric.news';
  const [items, cities, guides, vendors] = await Promise.all([
    getCollection('items'),
    getCollection('cities'),
    getCollection('guides'),
    getCollection('vendors'),
  ]);

  const iso = (d: Date) => d.toISOString().slice(0, 10);
  const newest = (list: { data: { published_at: Date } }[]) =>
    list.length ? iso(list.reduce((a, b) => (a.data.published_at > b.data.published_at ? a : b)).data.published_at) : undefined;

  const newestAll = newest(items);
  const newestNews = newest(items.filter((i) => i.data.content_type !== 'policy'));
  const newestPolicy = newest(items.filter((i) => i.data.content_type === 'policy'));

  type Entry = { loc: string; lastmod?: string; changefreq: string; priority: string };
  const entries: Entry[] = [
    { loc: '/', lastmod: newestAll, changefreq: 'daily', priority: '1.0' },
    { loc: '/news', lastmod: newestNews, changefreq: 'daily', priority: '0.9' },
    { loc: '/policy', lastmod: newestPolicy, changefreq: 'daily', priority: '0.9' },
    { loc: '/guides', changefreq: 'weekly', priority: '0.7' },
    { loc: '/resources', changefreq: 'weekly', priority: '0.7' },
    { loc: '/business-directory', changefreq: 'weekly', priority: '0.7' },
    { loc: '/about', changefreq: 'monthly', priority: '0.4' },
  ];

  for (const c of cities) {
    const cityItems = items.filter((i) => (i.data.municipalities as readonly string[]).includes(c.slug));
    entries.push({ loc: `/${c.slug}`, lastmod: newest(cityItems), changefreq: 'weekly', priority: '0.6' });
  }
  for (const g of guides) {
    entries.push({ loc: `/guides/${g.slug}`, lastmod: iso(g.data.updated_at ?? g.data.published_at), changefreq: 'monthly', priority: '0.6' });
  }
  for (const v of vendors) {
    entries.push({ loc: `/business-directory/${v.slug}`, lastmod: iso(v.data.last_verified), changefreq: 'monthly', priority: '0.4' });
  }

  const xml =
    `<?xml version="1.0" encoding="UTF-8"?>\n` +
    `<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n` +
    entries
      .map(
        (e) =>
          `  <url>\n    <loc>${site}${e.loc}</loc>\n` +
          (e.lastmod ? `    <lastmod>${e.lastmod}</lastmod>\n` : '') +
          `    <changefreq>${e.changefreq}</changefreq>\n    <priority>${e.priority}</priority>\n  </url>`,
      )
      .join('\n') +
    `\n</urlset>\n`;

  return new Response(xml, { headers: { 'Content-Type': 'application/xml; charset=utf-8' } });
}
