/**
 * Theme fidelity of the built site: every theme-driven page in dist/ renders the sections and
 * blocks its Shopify template renders (with the header and footer groups), nothing more, nothing
 * less, apart from the entries in ALLOWED_DIFFERENCES. Run after `npm run build`; skipped when
 * dist/ is absent.
 */
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { PRODUCT_TEMPLATE, rawExport } from '../src/lib/export-data.ts';
import { compareOutlines, expectedOutline, renderedOutline } from '../src/lib/fidelity.ts';
import { checkPageFidelity, fidelityData, fidelityPages, templateFor } from '../src/lib/fidelity-pages.ts';

const dist = join(import.meta.dirname, '..', 'dist');
const data = fidelityData(rawExport, new Date().getFullYear());
const pages = fidelityPages(rawExport, data);
const byTemplate = (template: string) => pages.filter((p) => p.template === template);

describe('which theme template renders each page', () => {
  it('renders every active product with the template its templateSuffix names, the one the site reads', () => {
    const products = rawExport.products.filter((p) => p.status === 'ACTIVE');
    expect(products.map((p) => templateFor('product', p.templateSuffix))).toEqual(products.map(() => PRODUCT_TEMPLATE));
    expect(byTemplate(PRODUCT_TEMPLATE)).toHaveLength(products.length);
  });

  it('covers home, 404, the three pages and every collection but the frontpage one, in export order', () => {
    expect(pages.filter((p) => !p.path.startsWith('/products/')).map((p) => `${p.path} ${p.template}`)).toEqual([
      '/ templates/index.json',
      '/404.html templates/404.json',
      '/contact/ templates/page.contact.json',
      '/our-collection/ templates/page.our-collection.json',
      '/our-story/ templates/page.our-story.json',
      ...rawExport.collections
        .filter((c) => c.handle !== 'frontpage')
        .map((c) => `${data.urls.collection(c.handle)} ${templateFor('collection', c.templateSuffix)}`),
    ]);
  });
});

describe.skipIf(!existsSync(dist))('built pages follow the theme exactly', () => {
  const check = (path: string) => {
    const page = pages.find((p) => p.path === path);
    if (!page) throw new Error(`no theme page ${path}`);
    expect(checkPageFidelity(dist, rawExport, data, page)).toEqual([]);
  };

  it('home (templates/index.json)', () => check('/'));
  it('Our Story (templates/page.our-story.json)', () => check('/our-story/'));
  it('Our Collection (templates/page.our-collection.json)', () => check('/our-collection/'));
  it('contact (templates/page.contact.json)', () => check('/contact/'));
  it('404 (templates/404.json)', () => check('/404.html'));
  it.each(byTemplate('templates/collection.json').map((p) => p.path))('collection %s (templates/collection.json)', check);
  it.each(byTemplate(PRODUCT_TEMPLATE).map((p) => p.path))(`product %s (${PRODUCT_TEMPLATE})`, check);
});

describe.skipIf(!existsSync(dist))('the check catches the homepage bug class', () => {
  const html = existsSync(dist) ? readFileSync(join(dist, 'index.html'), 'utf8') : '';
  const template = rawExport.theme.templates['templates/index.json'];
  const expected = expectedOutline(template?.sections ?? [], { template: 'templates/index.json' }, data);
  const homeOnly = (page: string) => {
    const nodes = renderedOutline(page).nodes;
    const ids = new Set((template?.sections ?? []).map((s) => s.id));
    return nodes.filter((n) => ids.has(n.id) || !n.id.startsWith('header') && !n.id.startsWith('footer'));
  };

  // Targets are read from the built page, never named: staff may rename or remove any section or button.
  const emptySection = /<div[^>]*data-theme-section="([^"]+)"[^>]*><\/div>/.exec(html);
  const button = /<p class="block-actions" data-theme-block="([^"]+)"[\s\S]*?<\/p>/.exec(html);
  const product = rawExport.products.find((p) => p.status === 'ACTIVE');

  it.skipIf(!emptySection || !product)('flags products injected into an empty theme section (the frontpage collection bug)', () => {
    const id = emptySection![1]!;
    const injected = html.replace(
      /(<div[^>]*data-theme-section="[^"]+"[^>]*>)<\/div>/,
      `$1<ul><li><a href="/products/${product!.handle}/">${product!.title}</a></li></ul></div>`,
    );
    expect(injected).not.toBe(html);
    const problems = compareOutlines(expected, homeOnly(injected));
    expect(problems.length).toBeGreaterThan(0);
    expect(problems.every((line) => line.startsWith(`theme ${id} `))).toBe(true);
  });

  it('flags an extra section', () => {
    const extra = html.replace('<footer', '<section data-theme-section="featured-products" data-theme-type="featured-products"></section><footer');
    expect(extra).not.toBe(html);
    expect(compareOutlines(expected, homeOnly(extra)).join(' | ')).toMatch(/site renders featured-products \[featured-products\]/);
  });

  it.skipIf(!button)('flags a dropped block', () => {
    const dropped = html.replace(button![0], '');
    expect(dropped).not.toBe(html);
    expect(compareOutlines(expected, homeOnly(dropped)).join(' | ')).toContain(`${button![1]!} [button] is missing on the site`);
  });
});
