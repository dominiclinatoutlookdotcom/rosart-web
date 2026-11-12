/**
 * The site's content code run on the live export (`export/`, whatever Shopify held at the last
 * export). Nothing here names a heading, a count or a file: every expectation is read from the
 * export itself, so a staff edit passes and only a broken site fails. Exact-value tests of the
 * same code run on the frozen snapshot (src/test-fixtures/export-snapshot).
 */
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { activeProducts, rawExport } from './export-data.ts';
import { collectionThemePage, parseCollections, parsePages, parseProducts, parseThemePages, productThemePage, usedImageFiles } from './site-content.ts';
import { collectImageFiles } from './used-images.ts';

const imagesDir = join(import.meta.dirname, '..', '..', 'export', 'images');

describe('the site content built from the live export', () => {
  it('has one parsed entry for every active product, collection and page, with the export handles', () => {
    expect(parseProducts(rawExport.products).map((p) => p.handle)).toEqual(activeProducts(rawExport).map((p) => p.handle));
    expect(parseCollections(rawExport.collections).map((c) => c.handle)).toEqual(rawExport.collections.filter((c) => c.handle !== 'frontpage').map((c) => c.handle));
    const pageHandles = parsePages(rawExport.pages).map((p) => p.handle);
    for (const handle of ['our-story', 'our-collection', 'contact']) expect(pageHandles).toContain(handle);
    for (const handle of pageHandles) expect(rawExport.pages.map((p) => p.handle)).toContain(handle);
  });

  it('builds a section model for every theme page, collection page and product page', () => {
    expect(parseThemePages(rawExport.theme).length).toBeGreaterThan(0);
    for (const c of rawExport.collections.filter((c) => c.handle !== 'frontpage')) expect(collectionThemePage(c.handle).sections.length, c.handle).toBeGreaterThan(0);
    for (const p of activeProducts(rawExport)) expect(productThemePage(p.handle).sections.length, p.handle).toBeGreaterThan(0);
  });

  it('uses only image files that exist in export/images/ and in the manifest', () => {
    const used = usedImageFiles();
    expect(used.length).toBeGreaterThan(0);
    for (const file of used) {
      expect(existsSync(join(imagesDir, file)), `${file} on disk`).toBe(true);
      expect(rawExport.manifest.images[file], `${file} in manifest`).toBeDefined();
    }
  });

  it('uses every image its own products and collections carry', () => {
    const used = new Set(usedImageFiles());
    const own = collectImageFiles(parseProducts(rawExport.products), parseCollections(rawExport.collections));
    for (const file of own) expect(used.has(file), file).toBe(true);
  });
});
