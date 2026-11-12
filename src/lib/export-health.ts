/**
 * Is the exported Shopify content enough to build a working site? Checks structure only: the
 * sections, templates, required fields and image files the build needs. It never compares a value
 * with a literal, so staff editing copy in Shopify (a heading, intro text, a logo list, product
 * titles) can never fail it; a missing section or an image referenced but absent does.
 */
import { createImageResolver } from './images.ts';
import { PRODUCT_TEMPLATE } from './export-data.ts';
import type { RawExport } from './shopify-types.ts';

/** Templates and section groups the site renders (`THEME_PAGES` in site-content.ts, the layout). */
export const REQUIRED_TEMPLATES = [
  'templates/index.json',
  'templates/404.json',
  'templates/page.our-story.json',
  'templates/page.our-collection.json',
  'templates/page.contact.json',
  'templates/collection.json',
  PRODUCT_TEMPLATE,
] as const;
export const REQUIRED_SECTION_GROUPS = ['sections/header-group.json', 'sections/footer-group.json'] as const;
export const REQUIRED_PAGES = ['our-story', 'our-collection', 'contact'] as const;
export const REQUIRED_MENU = 'main-menu';

const filled = (value: unknown): boolean => typeof value === 'string' && value.trim() !== '';

/** Every string anywhere in a JSON value. */
function strings(value: unknown, out: string[] = []): string[] {
  if (typeof value === 'string') out.push(value);
  else if (Array.isArray(value)) value.forEach((v) => strings(v, out));
  else if (typeof value === 'object' && value !== null) Object.values(value).forEach((v) => strings(v, out));
  return out;
}

/**
 * @param imageExists whether an image file is present in `export/images/` (injected: pure, no disk access here)
 * @returns one line per problem; empty when the export can build a site
 */
export function exportProblems(raw: RawExport, imageExists: (file: string) => boolean): string[] {
  const problems: string[] = [];
  const need = (ok: boolean, message: string): void => {
    if (!ok) problems.push(message);
  };

  need(filled(raw.shop?.name), 'shop.json: the shop name is empty');
  need(filled(raw.shop?.currencyCode), 'shop.json: the currency code is empty');
  need(filled(raw.shop?.myshopifyDomain), 'shop.json: myshopifyDomain is empty');
  need(filled(raw.shop?.primaryDomain?.host), 'shop.json: primaryDomain.host is empty');

  const templates = raw.theme?.templates ?? {};
  for (const name of REQUIRED_TEMPLATES) need(name in templates, `theme-content.json: no ${name}`);
  for (const name of REQUIRED_SECTION_GROUPS) need(name in (raw.theme?.section_groups ?? {}), `theme-content.json: no ${name}`);
  const home = templates['templates/index.json'];
  need(home === undefined || home.sections.some((s) => !s.disabled), 'templates/index.json: the homepage has no enabled section');

  const resolver = createImageResolver(raw.manifest, raw.files ?? [], {});
  const checkFile = (file: string, where: string): void => {
    need(imageExists(file), `${where}: image file ${file} is missing from export/images/`);
  };
  const checkImage = (ref: () => { file: string }, where: string): void => {
    try {
      checkFile(ref().file, where);
    } catch (error) {
      problems.push(`${where}: ${(error as Error).message}`);
    }
  };

  const active = (raw.products ?? []).filter((p) => p.status === 'ACTIVE');
  need(active.length > 0, 'products.json: no active product');
  for (const product of active) {
    const where = `product ${product.handle || '(no handle)'}`;
    need(filled(product.handle), `${where}: empty handle`);
    need(filled(product.title), `${where}: empty title`);
    need(product.images.length > 0, `${where}: no image`);
    const template = product.templateSuffix ? `templates/product.${product.templateSuffix}.json` : 'templates/product.json';
    need(template === PRODUCT_TEMPLATE, `${where}: uses ${template}; the site reads ${PRODUCT_TEMPLATE}`);
    product.images.forEach((image, i) => checkImage(() => resolver.fromUrl(image.url), `${where} image ${i + 1}`));
  }

  for (const collection of raw.collections ?? []) {
    const where = `collection ${collection.handle || '(no handle)'}`;
    need(filled(collection.handle) && filled(collection.title), `${where}: empty handle or title`);
    if (collection.templateSuffix) need(`templates/collection.${collection.templateSuffix}.json` in templates, `${where}: template ${collection.templateSuffix} is not exported`);
    if (collection.image) checkImage(() => resolver.fromUrl(collection.image!.url), `${where} image`);
  }

  for (const handle of REQUIRED_PAGES) {
    const page = (raw.pages ?? []).find((p) => p.handle === handle);
    need(page !== undefined && page.isPublished && filled(page.title), `pages.json: required page ${handle} is missing, unpublished or untitled`);
  }

  const menu = (raw.menus ?? []).find((m) => m.handle === REQUIRED_MENU);
  need(menu !== undefined && menu.items.length > 0, `menus.json: ${REQUIRED_MENU} is missing or empty`);

  // Every image the theme picks (logo, hero and section images, the logos in custom HTML).
  const seen = new Set<string>();
  for (const text of strings(raw.theme)) {
    for (const match of text.matchAll(/shopify:\/\/shop_images\/[^"'\s)<>]+|https:\/\/cdn\.shopify\.com\/[^"'\s)<>]+\.(?:png|jpe?g|webp|gif|svg)/gi)) {
      const ref = match[0];
      if (seen.has(ref)) continue;
      seen.add(ref);
      checkImage(() => resolver.resolve(ref), `theme image ${ref}`);
    }
  }
  return problems;
}
