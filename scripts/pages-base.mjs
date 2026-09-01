// Prefix root-relative links in a built site with a base path.
//
// GitHub Pages serves project sites under /<repo>/, but the site is authored
// for a real domain where everything lives at "/". Rather than thread a base
// path through every component, we build normally and rewrite the output.
//
// Usage: node scripts/pages-base.mjs dist /bric-news
//
// Touches: href="/x", src="/x", action="/x", srcset="/x", url(/x) in CSS.
// Leaves alone: "//cdn", "http(s)://", "#hash", and anything already prefixed.

import { readdirSync, readFileSync, writeFileSync, statSync } from 'node:fs';
import { join, extname } from 'node:path';

const [, , dir = 'dist', baseArg = '/'] = process.argv;
const base = baseArg.replace(/\/$/, '');
if (!base) {
  console.log('No base path given; nothing to do.');
  process.exit(0);
}

const TEXT = new Set(['.html', '.css', '.xml', '.txt', '.webmanifest']);
let files = 0;
let edits = 0;

function walk(d) {
  for (const name of readdirSync(d)) {
    const p = join(d, name);
    if (statSync(p).isDirectory()) walk(p);
    else if (TEXT.has(extname(name))) rewrite(p);
  }
}

function rewrite(file) {
  const before = readFileSync(file, 'utf8');
  let after = before;
  // Attributes: href="/..." src="/..." action="/..." (not "//", not already based)
  after = after.replace(
    new RegExp(`\\b(href|src|action)="/(?!/|${base.slice(1)}/)`, 'g'),
    (_, attr) => `${attr}="${base}/`,
  );
  // srcset can hold several URLs
  after = after.replace(/\bsrcset="([^"]+)"/g, (_, list) =>
    `srcset="${list.replace(/(^|,\s*)\/(?!\/)/g, `$1${base}/`)}"`,
  );
  // CSS url(/...)
  after = after.replace(/url\((['"]?)\/(?!\/)/g, (_, q) => `url(${q}${base}/`);
  if (after !== before) {
    writeFileSync(file, after);
    edits += 1;
  }
  files += 1;
}

walk(dir);
console.log(`Prefixed links with ${base}/ in ${edits} of ${files} files.`);
