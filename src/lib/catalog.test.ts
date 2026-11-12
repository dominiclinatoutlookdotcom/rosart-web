import { describe, expect, it, vi } from 'vitest';
import { createSiteContext, rawExport } from './export-data.ts';
import { toCollections, toPages, toProducts } from './catalog.ts';

// Exact-value tests run on the frozen snapshot, never on the live export (src/test-fixtures/export-snapshot).
vi.mock('./export-data.ts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./export-data.ts')>();
  const { fixtureExport } = await import('../test-fixtures/export-snapshot/load.ts');
  return { ...actual, rawExport: fixtureExport };
});

const ctx = createSiteContext(rawExport, { imageAlt: {} });
const products = toProducts(rawExport.products, ctx);
const collections = toCollections(rawExport.collections, ctx);
const pages = toPages(rawExport.pages, ctx);
const rawProduct = (handle: string) => {
  const found = rawExport.products.find((p) => p.handle === handle);
  if (!found) throw new Error(`no raw product ${handle}`);
  return found;
};
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

  it('resolves every product image to a file in the image manifest, with the Shopify alt text', () => {
    const shopifyAlts = rawExport.products.flatMap((p) => p.images.map((i) => i.altText?.trim() ?? '')).filter(Boolean);
    expect(shopifyAlts.length).toBeGreaterThan(0); // the export holds alt texts, so ignoring them would fail below
    for (const product of products) {
      const raw = rawProduct(product.handle);
      expect(product.images.length).toBe(raw.images.length);
      product.images.forEach((image, index) => {
        expect(rawExport.manifest.images[image.file], image.file).toBeDefined();
        expect(image.alt, image.file).toBe(raw.images[index]?.altText?.trim() || product.title);
      });
    }
  });

  it('falls back to the product title for an image Shopify holds no alt text for', () => {
    const raw = rawProduct('rose-face-cream-50ml');
    const [first, second] = raw.images;
    if (!first || !second) throw new Error('fixture product needs two images');
    // No files.json alt either: otherwise that alt is the next fallback.
    const bare = createSiteContext({ ...rawExport, files: [] }, { imageAlt: {} });
    const images = [{ ...first, altText: 'The jar on a sunlit table' }, { ...second, altText: null }];
    const [product] = toProducts([{ ...raw, images }], bare);
    expect(product?.images.map((i) => i.alt)).toEqual(['The jar on a sunlit table', raw.title]);
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

  it('uses the Shopify SEO fields from the export and the single variant price', () => {
    const water = bySlug('100-natural-damascena-rose-water-10ml');
    const raw = rawProduct('100-natural-damascena-rose-water-10ml');
    const tag = (key: string) => raw.metafields.find((m) => m.namespace === 'global' && m.key === key)?.value;
    const seoTitle = raw.seo.title || tag('title_tag');
    const seoDescription = raw.seo.description || tag('description_tag');
    // The export sets both, unlike the defaults, so ignoring them would fail.
    expect(seoTitle).toBeTruthy();
    expect(seoTitle).not.toBe(raw.title);
    expect(seoDescription).toBeTruthy();
    expect(water.seo.title).toBe(seoTitle);
    expect(water.seo.description).toBe(seoDescription);
    expect(water.price).toEqual({ amount: '10.00', currencyCode: 'AUD' });
    expect(water.sku).toBe('RARWT10ML');
  });

  it('takes the SEO title from seo.title, else the global.title_tag metafield, else the product title', () => {
    const raw = rawProduct('rose-face-cream-50ml');
    const base = { ...raw, metafields: raw.metafields.filter((m) => m.namespace !== 'global') };
    const titleOf = (product: typeof raw) => toProducts([product], ctx)[0]?.seo.title;
    const titleTag = { namespace: 'global', key: 'title_tag', type: 'single_line_text_field', value: 'Tag title' };
    expect(titleOf({ ...base, seo: { title: 'SEO title', description: null }, metafields: [...base.metafields, titleTag] })).toBe('SEO title');
    expect(titleOf({ ...base, seo: { title: null, description: null }, metafields: [...base.metafields, titleTag] })).toBe('Tag title');
    expect(titleOf({ ...base, seo: { title: null, description: null } })).toBe(raw.title);
  });

  it('gives each product its collection for the breadcrumb, never the homepage list', () => {
    expect(bySlug('rose-facial-toner-330ml').collection).toEqual({
      title: 'Rose Water & Toners',
      url: '/collections/rose-water-toners/',
    });
    expect(bySlug('rosart-rose-face-mask-80ml').collection?.url).toBe('/collections/masks-treatments/');
  });

  it('links every product to the ORIZEN search for its title while no orizen_product_url metafield is set', () => {
    expect(bySlug('rose-face-cream-50ml').orizenUrl).toBe(
      `https://orizen.com.au/search?q=${encodeURIComponent(bySlug('rose-face-cream-50ml').title)}`,
    );
  });

  it('links to the custom.orizen_product_url metafield (the link the theme SHOP NOW button reads) when it is set and on orizen.com.au', () => {
    const raw = rawExport.products.find((p) => p.handle === 'rose-face-cream-50ml');
    if (!raw) throw new Error('fixture product missing');
    const withUrl = (value: string) => ({
      ...raw,
      metafields: [...raw.metafields, { namespace: 'custom', key: 'orizen_product_url', type: 'url', value }],
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
