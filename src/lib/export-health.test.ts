import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { fixtureExport } from '../../tests/fixtures/export-snapshot/load.ts';
import { exportProblems, REQUIRED_PAGES, REQUIRED_TEMPLATES } from './export-health.ts';
import { PRODUCT_TEMPLATE, rawExport } from './export-data.ts';
import type { RawExport } from './shopify-types.ts';

const imagesDir = join(import.meta.dirname, '..', '..', 'export', 'images');

// The snapshot holds JSON only, so "the file exists" means "the manifest lists it".
const inManifest = (raw: RawExport) => {
  const listed = new Set(Object.keys(raw.manifest.images));
  return (file: string) => listed.has(file);
};
const clone = (): RawExport => structuredClone(fixtureExport);
const problemsOf = (raw: RawExport, exists = inManifest(raw)) => exportProblems(raw, exists);

/** Rewrite every string of the theme the way a staff edit in the theme editor would. */
const editThemeText = (raw: RawExport, from: string, to: string): RawExport => ({
  ...raw,
  theme: JSON.parse(JSON.stringify(raw.theme).split(from).join(to)),
});

describe('the live export (what the publish checks)', () => {
  it('can build a site: every required section, field and image file is present', () => {
    expect(exportProblems(rawExport, (file) => existsSync(join(imagesDir, file)))).toEqual([]);
  });
});

describe('exportProblems accepts content a staff member edited in Shopify', () => {
  it('passes the unedited snapshot', () => {
    expect(problemsOf(clone())).toEqual([]);
  });

  it('passes a changed homepage/connections heading and intro text (the 2026-11-12 job 8 failure)', () => {
    const edited = editThemeText(editThemeText(clone(), 'Our Global Connections', 'From Bulgaria to Australia'), 'Rooted in Bulgaria', 'Grown in Bulgaria');
    expect(JSON.stringify(edited.theme)).toContain('From Bulgaria to Australia');
    expect(problemsOf(edited)).toEqual([]);
  });

  it('passes a changed logo list: a logo swapped for another exported file', () => {
    const edited = editThemeText(clone(), 'files/ARTEC.png', 'files/ROSART.png');
    expect(JSON.stringify(edited.theme)).not.toContain('files/ARTEC.png');
    expect(problemsOf(edited)).toEqual([]);
  });

  it('passes renamed products, a removed product, a new collection title and a shorter menu', () => {
    const edited = clone();
    edited.products[0]!.title = 'A product staff renamed';
    edited.products.pop();
    edited.collections[0]!.title = 'A collection staff renamed';
    edited.menus.find((m) => m.handle === 'main-menu')!.items.length = 1;
    expect(problemsOf(edited)).toEqual([]);
  });
});

describe('exportProblems rejects an export that would build a broken site', () => {
  it('flags a missing required theme template', () => {
    for (const name of REQUIRED_TEMPLATES) {
      const broken = clone();
      delete broken.theme.templates[name];
      expect(problemsOf(broken), name).toEqual([`theme-content.json: no ${name}`]);
    }
  });

  it('flags a homepage with no enabled section', () => {
    const broken = clone();
    for (const section of broken.theme.templates['templates/index.json']!.sections) section.disabled = true;
    expect(problemsOf(broken)).toEqual(['templates/index.json: the homepage has no enabled section']);
  });

  it('flags an image the export references but does not hold', () => {
    const broken = clone();
    const file = broken.manifest.shop_images['ESPRIT_HONG_KONG.png']!;
    const missing = problemsOf(broken, (f) => f !== file);
    expect(missing).toContain(`theme image shopify://shop_images/ESPRIT_HONG_KONG.png: image file ${file} is missing from export/images/`);
  });

  it('flags a theme image the manifest does not map', () => {
    const broken = clone();
    delete broken.manifest.shop_images['ESPRIT_HONG_KONG.png'];
    expect(problemsOf(broken).join('\n')).toMatch(/ESPRIT_HONG_KONG\.png is not in export\/images\/manifest\.json/);
  });

  it('flags a product image the manifest does not map', () => {
    const broken = clone();
    broken.manifest.sources = {};
    expect(problemsOf(broken).join('\n')).toMatch(/product .* image 1: Image .* is not in export\/images\/manifest\.json/);
  });

  it('flags empty required fields: shop name, product title, product handle', () => {
    const broken = clone();
    broken.shop.name = ' ';
    broken.products[0]!.title = '';
    expect(problemsOf(broken)).toEqual([
      'shop.json: the shop name is empty',
      `product ${broken.products[0]!.handle}: empty title`,
    ]);
  });

  it('flags a product with no images and a catalogue with no active product', () => {
    const noImages = clone();
    noImages.products[0]!.images = [];
    expect(problemsOf(noImages)).toEqual([`product ${noImages.products[0]!.handle}: no image`]);
    const none = clone();
    none.products.forEach((p) => (p.status = 'DRAFT'));
    expect(problemsOf(none)).toEqual(['products.json: no active product']);
  });

  it('flags a required page that is missing or unpublished, and a missing main menu', () => {
    for (const handle of REQUIRED_PAGES) {
      const gone = clone();
      gone.pages = gone.pages.filter((p) => p.handle !== handle);
      expect(problemsOf(gone), handle).toHaveLength(1);
      const hidden = clone();
      hidden.pages.find((p) => p.handle === handle)!.isPublished = false;
      expect(problemsOf(hidden), handle).toHaveLength(1);
    }
    const noMenu = clone();
    noMenu.menus = noMenu.menus.filter((m) => m.handle !== 'main-menu');
    expect(problemsOf(noMenu)).toEqual(['menus.json: main-menu is missing or empty']);
  });

  // Intended guard (owner decision): products must use the catalogue template, as createSiteContext also enforces.
  it('flags a product that does not use the catalogue template', () => {
    const broken = clone();
    broken.products[0]!.templateSuffix = null;
    expect(problemsOf(broken)).toEqual([
      `product ${broken.products[0]!.handle}: uses templates/product.json; the site reads ${PRODUCT_TEMPLATE}`,
    ]);
  });
});
