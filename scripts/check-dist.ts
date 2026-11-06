/**
 * `npm run check:dist`: checks the built `dist/` before upload. See `dist-check.ts` for the rules.
 * Expected pages come from the export through the same URL map the site uses.
 */
import { existsSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { resolveSiteUrl } from '../src/lib/commerce.ts';
import { createUrlMap } from '../src/lib/urls.ts';
import { siteConfig } from '../src/site.config.ts';
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

const requiredPages = [...urls.allUrls(), '/404.html', '/robots.txt', '/sitemap-index.xml'];
const problems = checkDist(dist, {
  requiredPages,
  siteUrl: resolveSiteUrl(process.env['SITE_URL']),
  forbiddenHashes: new Set(themeFiles.map((f) => sha256(readFileSync(f)))),
  maxImageBytes: 600 * 1024,
  // One email for the whole site: an address in Shopify content is fixed in Shopify, not here.
  allowedEmails: [siteConfig.enquiryEmail],
});

if (problems.length > 0) {
  console.error(`dist check failed with ${problems.length} problem(s):`);
  for (const problem of problems) console.error(`  - ${problem}`);
  process.exit(1);
}
const files = listFiles(dist);
const bytes = files.reduce((sum, f) => sum + statSync(f).size, 0);
console.log(
  `dist check passed: ${requiredPages.length} required pages, ${files.length} files, ${(bytes / 1024 / 1024).toFixed(1)} MB`,
);
