/**
 * `npm run check:dist`: checks the built `dist/` before upload. See `dist-check.ts` for the rules.
 * Expected pages come from the export through the same URL map the site uses.
 */
import { existsSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { resolveSiteUrl } from '../src/lib/commerce.ts';
import { createUrlMap } from '../src/lib/urls.ts';
import { siteConfig } from '../src/site.config.ts';
import { checkPageFidelity, fidelityData, fidelityPages } from '../src/lib/fidelity-pages.ts';
import type { RawExport } from '../src/lib/shopify-types.ts';
import { checkDist, listFiles, sha256 } from './dist-check.ts';

const root = join(import.meta.dirname, '..');
const dist = join(root, 'dist');
const readJson = <T>(path: string): T => JSON.parse(readFileSync(join(root, path), 'utf8')) as T;

if (!existsSync(dist)) {
  console.error('dist/ does not exist: run `npm run build` first');
  process.exit(1);
}

interface Handle {
  handle: string;
  title: string;
  status?: string;
  isPublished?: boolean;
}
const shop = readJson<{ myshopifyDomain: string; primaryDomain: { host: string } }>('export/shopify/shop.json');
const urls = createUrlMap({
  shopDomains: [shop.myshopifyDomain, shop.primaryDomain.host],
  pages: readJson<Handle[]>('export/shopify/pages.json').filter((p) => p.isPublished),
  collections: readJson<Handle[]>('export/shopify/collections.json'),
  products: readJson<Handle[]>('export/shopify/products.json').filter((p) => p.status === 'ACTIVE'),
});

const themeDir = join(root, 'export', 'theme');
const themeFiles = listFiles(themeDir).filter((f) => statSync(f).size > 0);
if (themeFiles.length === 0) console.warn('export/theme/ is absent: skipping the copied-theme-file check');

const requiredPages = [...urls.allUrls(), '/404.html', '/robots.txt', '/llms.txt', '/sitemap-index.xml'];
const problems = checkDist(dist, {
  requiredPages,
  siteUrl: resolveSiteUrl(process.env['SITE_URL']),
  forbiddenHashes: new Set(themeFiles.map((f) => sha256(readFileSync(f)))),
  maxImageBytes: 600 * 1024,
  // One email for the whole site: an address in Shopify content is fixed in Shopify, not here.
  allowedEmails: [siteConfig.enquiryEmail],
});

// Theme fidelity: every theme-driven page renders exactly what its Shopify template renders.
const rawExport: RawExport = {
  shop: readJson('export/shopify/shop.json'),
  products: readJson('export/shopify/products.json'),
  collections: readJson('export/shopify/collections.json'),
  pages: readJson('export/shopify/pages.json'),
  menus: readJson('export/shopify/menus.json'),
  files: readJson('export/shopify/files.json'),
  manifest: readJson('export/images/manifest.json'),
  theme: readJson('export/theme-content.json'),
};
const fidelity = fidelityData(rawExport, new Date().getFullYear());
const themePages = fidelityPages(rawExport, fidelity);
for (const page of themePages) problems.push(...checkPageFidelity(dist, rawExport, fidelity, page));

if (problems.length > 0) {
  console.error(`dist check failed with ${problems.length} problem(s):`);
  for (const problem of problems) console.error(`  - ${problem}`);
  process.exit(1);
}
const files = listFiles(dist);
const bytes = files.reduce((sum, f) => sum + statSync(f).size, 0);
console.log(
  `dist check passed: ${requiredPages.length} required pages, ${themePages.length} pages match the theme, ${files.length} files, ${(bytes / 1024 / 1024).toFixed(1)} MB`,
);
