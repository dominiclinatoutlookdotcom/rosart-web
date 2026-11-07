/**
 * Which theme template renders each site page, and the check of every built page against it:
 * header group, the page's template, footer group, as Shopify composes them. Runs under plain
 * Node too (the dist check).
 */
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { compareOutlines, expectedOutline, renderedOutline, type FidelityData, type Scope } from './fidelity.ts';
import { createImageResolver } from './images.ts';
import type { RawExport, RawThemeSection } from './shopify-types.ts';
import { createUrlMap } from './urls.ts';

export interface FidelityPage {
  /** The site path, such as `/products/x/` or `/404.html`. */
  path: string;
  /** The theme template Shopify renders it with. */
  template: string;
  scope: Scope;
}

/** `templates/<kind>.<suffix>.json` when the resource names a suffix, else `templates/<kind>.json`. */
export function templateFor(kind: 'product' | 'collection' | 'page', suffix: string | null | undefined): string {
  return suffix ? `templates/${kind}.${suffix}.json` : `templates/${kind}.json`;
}

export function fidelityData(raw: RawExport, year: number): FidelityData {
  const products = raw.products.filter((p) => p.status === 'ACTIVE');
  const logo = raw.theme.global_settings['logo'];
  return {
    urls: createUrlMap({
      shopDomains: [raw.shop.myshopifyDomain, raw.shop.primaryDomain.host],
      pages: raw.pages.filter((p) => p.isPublished),
      collections: raw.collections,
      products,
    }),
    images: createImageResolver(raw.manifest, raw.files, {}),
    products,
    collections: raw.collections,
    menus: raw.menus,
    shopName: raw.shop.name,
    logo: typeof logo === 'string' && logo ? logo : null,
    year,
  };
}

/** Every theme-driven page of the site, with the template and `closest.*` data Shopify uses. */
export function fidelityPages(raw: RawExport, data: FidelityData): FidelityPage[] {
  const pages: FidelityPage[] = [
    { path: '/', template: 'templates/index.json', scope: { template: 'templates/index.json' } },
    { path: '/404.html', template: 'templates/404.json', scope: { template: 'templates/404.json' } },
  ];
  for (const page of raw.pages) {
    const path = page.isPublished ? data.urls.page(page.handle) : undefined;
    if (!path) continue; // the Shopify-only privacy-choices page has no site page
    const template = templateFor('page', page.templateSuffix);
    pages.push({ path, template, scope: { template, page } });
  }
  for (const collection of raw.collections) {
    const path = data.urls.collectionSlug(collection.handle) ? data.urls.collection(collection.handle) : undefined;
    if (!path) continue; // the homepage collection has no page of its own
    const template = templateFor('collection', collection.templateSuffix);
    pages.push({ path, template, scope: { template, collection } });
  }
  for (const product of data.products) {
    const path = data.urls.product(product.handle);
    if (!path) continue;
    const template = templateFor('product', product.templateSuffix);
    pages.push({ path, template, scope: { template, product } });
  }
  return pages;
}

function groupSections(raw: RawExport, name: string): RawThemeSection[] {
  return raw.theme.section_groups[`sections/${name}-group.json`]?.sections ?? [];
}

function distFile(dist: string, path: string): string {
  return path.endsWith('/') ? join(dist, path, 'index.html') : join(dist, path);
}

/** Problems with one built page: its outline against the theme's, and content outside the outline. */
export function checkPageFidelity(dist: string, raw: RawExport, data: FidelityData, page: FidelityPage): string[] {
  const file = distFile(dist, page.path);
  if (!existsSync(file)) return [`${page.path}: not built`];
  const template = raw.theme.templates[page.template];
  if (!template) return [`${page.path}: theme-content.json has no ${page.template}`];
  const expected = [
    ...expectedOutline(groupSections(raw, 'header'), { ...page.scope, template: 'sections/header-group.json' }, data),
    ...expectedOutline(template.sections, page.scope, data),
    ...expectedOutline(groupSections(raw, 'footer'), { ...page.scope, template: 'sections/footer-group.json' }, data),
  ];
  const rendered = renderedOutline(readFileSync(file, 'utf8'));
  return [...rendered.problems, ...compareOutlines(expected, rendered.nodes)].map((p) => `${page.path} (${page.template}): ${p}`);
}
