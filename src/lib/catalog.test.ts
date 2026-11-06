import { existsSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { createSiteContext, rawExport } from './export-data.ts';
import { featuredProductHandles, toCollections, toPages, toProducts } from './catalog.ts';

const ctx = createSiteContext(rawExport, { imageAlt: {} });
const products = toProducts(rawExport.products, ctx);
const collections = toCollections(rawExport.collections, ctx);
const pages = toPages(rawExport.pages, ctx);
const bySlug = (handle: string) => {
  const found = products.find((p) => p.handle === handle);
  if (!found) throw new Error(`no product ${handle}`);
  return found;
};

describe('toProducts', () => {
  it('loads every active product with its site URL', () => {
    expect(products).toHaveLength(14);
    expect(bySlug('rose-face-cream-50ml').url).toBe('/products/rose-face-cream-50ml/');
  });

  it('resolves every product image to a file in export/images, with alt text', () => {
    for (const product of products) {
      expect(product.images.length).toBeGreaterThan(0);
      for (const image of product.images) {
        expect(existsSync(`export/images/${image.file}`), image.file).toBe(true);
        expect(image.alt).toBe(product.title);
      }
    }
  });

  it('renders the detail sections in the theme order with the theme headings', () => {
    const water = bySlug('100-natural-damascena-rose-water-10ml');
    expect(water.details.map((d) => d.heading)).toEqual([
      'HOW TO USE',
      'THE INGREDIENTS',
      'CERTIFICATIONS & BRAND VALUES',
      'CAUTION',
    ]);
    const caution = water.details[3]?.html ?? '';
    expect(caution.startsWith('<p>For external use only.</p>')).toBe(true);
    expect(water.details[0]?.html).toMatch(/^<p>Spray directly onto the face/);
  });

  it('uses the Shopify SEO fields and the single variant price', () => {
    const water = bySlug('100-natural-damascena-rose-water-10ml');
    expect(water.seo.title).toBe('Rosart Rose Water 10ml | 100% Natural Bulgarian Rose Water');
    expect(water.seo.description).toMatch(/^Discover Rosart Rose Water 10ml/);
    expect(water.price).toEqual({ amount: '10.00', currencyCode: 'AUD' });
    expect(water.sku).toBe('RARWT10ML');
  });

  it('gives each product its collection for the breadcrumb, never the homepage list', () => {
    expect(bySlug('rose-facial-toner-330ml').collection).toEqual({
      title: 'Rose Water & Toners',
      url: '/collections/rose-water-toners/',
    });
    expect(bySlug('rosart-rose-face-mask-80ml').collection?.url).toBe('/collections/masks-treatments/');
  });

  it('links every product to the ORIZEN search for its title while no orizen_url metafield is set', () => {
    expect(bySlug('rose-face-cream-50ml').orizenUrl).toBe(
      `https://orizen.com.au/search?q=${encodeURIComponent(bySlug('rose-face-cream-50ml').title)}`,
    );
  });

  it('links to the custom.orizen_url metafield when it is set and on orizen.com.au', () => {
    const raw = rawExport.products.find((p) => p.handle === 'rose-face-cream-50ml');
    if (!raw) throw new Error('fixture product missing');
    const withUrl = (value: string) => ({
      ...raw,
      metafields: [...raw.metafields, { namespace: 'custom', key: 'orizen_url', type: 'url', value }],
    });
    const [linked] = toProducts([withUrl('https://orizen.com.au/products/rose-face-cream-50ml')], ctx);
    expect(linked?.orizenUrl).toBe('https://orizen.com.au/products/rose-face-cream-50ml');
    const [elsewhere] = toProducts([withUrl('https://example.com/rose-face-cream')], ctx);
    expect(elsewhere?.orizenUrl).toMatch(/^https:\/\/orizen\.com\.au\/search\?q=/);
  });

  it('keeps descriptions as clean HTML with no store references', () => {
    for (const product of products) {
      expect(product.descriptionHtml).not.toMatch(/style=|cdn\.shopify|myshopify/);
    }
  });
});

describe('toCollections', () => {
  it('loads the three collections with clean slugs and drops the homepage list', () => {
    expect(collections.map((c) => [c.slug, c.title])).toEqual([
      ['face-care', 'Face Care'],
      ['rose-water-toners', 'Rose Water & Toners'],
      ['masks-treatments', 'Masks & Treatments'],
    ]);
  });

  it('keeps product order and the collection image', () => {
    const masks = collections.find((c) => c.slug === 'masks-treatments');
    expect(masks?.productHandles[0]).toBe('pure-face-rosehip-oil-2ml');
    expect(masks?.image?.file).toBe('3_e77f03d4-0767-4f83-939c-86d99bde2338-2.png');
  });
});

describe('featuredProductHandles', () => {
  it('returns the homepage collection products in order', () => {
    const featured = featuredProductHandles(rawExport.collections);
    expect(featured).toHaveLength(8);
    expect(featured[0]).toBe('bulgarian-rose-hydrosol-essence-mask-5pcs');
  });
});

describe('toPages', () => {
  it('loads the site pages and skips the Shopify privacy-choices page', () => {
    expect(pages.map((p) => p.handle).sort()).toEqual(['contact', 'our-collection', 'our-story']);
  });

  it('sanitises the contact body', () => {
    const contact = pages.find((p) => p.handle === 'contact');
    expect(contact?.bodyHtml).toContain('<p>We&#39;d love to hear from you.</p>');
    expect(contact?.bodyHtml).not.toMatch(/style=|data-pm-slice/);
  });
});
