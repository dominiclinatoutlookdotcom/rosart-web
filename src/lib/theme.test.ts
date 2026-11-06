import { describe, expect, it } from 'vitest';
import { toCollections } from './catalog.ts';
import { createSiteContext, rawExport } from './export-data.ts';
import type { RawThemeSection } from './shopify-types.ts';
import {
  describeSections,
  firstBackground,
  productPurchaseNote,
  recommendationsHeading,
  siteChrome,
  toNavigation,
  toSectionModel,
  withFeaturedProducts,
  type Block,
  type Section,
} from './theme.ts';

const ctx = createSiteContext(rawExport, { imageAlt: {} });
const themeCtx = { ...ctx, siteCollections: toCollections(rawExport.collections, ctx) };
const template = (name: string) => {
  const found = rawExport.theme.templates[`templates/${name}.json`];
  if (!found) throw new Error(name);
  return found;
};
const kinds = (sections: Section[]) => sections.map((s) => s.kind);
const blocksOf = (section: Section | undefined): Block[] => (section && 'blocks' in section ? section.blocks : []);

describe('toSectionModel: homepage', () => {
  const { sections, skipped } = toSectionModel(template('index'), themeCtx, { firstHeadingIsH1: true });

  it('keeps the owner section order and drops the empty spacer', () => {
    expect(kinds(sections)).toEqual(['hero', 'content', 'content', 'content', 'content', 'content']);
    expect(skipped).toEqual([]);
  });

  it('builds the hero with desktop and mobile images, the h1, and no disabled button', () => {
    const hero = sections[0];
    if (hero?.kind !== 'hero') throw new Error('no hero');
    expect(hero.image?.file).toBe('Untitled_design_7_f6c26f2b-ad40-440e-a57a-fb6da4fe0dcb.png');
    expect(hero.mobileImage?.file).toBe('DSC08666_Rosart_Mobile_1200x1500_e230edb1-8068-47e5-b216-efa8170e4f7e.jpg');
    expect(hero.blocks).toEqual([
      { kind: 'heading', text: 'From Soil to Skin', level: 1 },
      { kind: 'richtext', html: '<p>From Our Rose Fields to Your Skin.</p>' },
    ]);
  });

  it('turns later headings into h2 and keeps body text', () => {
    expect(blocksOf(sections[1])[0]).toEqual({ kind: 'heading', text: 'Rooted in the Valley of Roses', level: 2 });
    expect(blocksOf(sections[1])[1]).toMatchObject({ kind: 'richtext' });
  });

  it('keeps the image and text group, and the certifications image on its background', () => {
    expect(blocksOf(sections[2]).map((b) => b.kind)).toEqual(['image', 'group']);
    const certifications = sections[3];
    if (certifications?.kind !== 'content') throw new Error('no certifications section');
    expect(certifications.background?.file).toBe('bg1.png');
    expect(certifications.blocks[0]).toMatchObject({ kind: 'image', image: { file: 'USDA_and_EU_Organic_Certifications.png' } });
  });

  it('links the three collection tiles to their collection pages', () => {
    const tiles = blocksOf(sections[4]);
    expect(tiles.map((b) => (b.kind === 'group' ? b.href : undefined))).toEqual([
      '/collections/face-care/',
      '/collections/rose-water-toners/',
      '/collections/masks-treatments/',
    ]);
  });

  it('builds the closing band with eyebrow, heading and the Our Story button', () => {
    const band = sections[5];
    if (band?.kind !== 'content') throw new Error('no band');
    expect(band.background?.file).toBe('bg3.png');
    expect(band.blocks.map((b) => b.kind)).toEqual(['eyebrow', 'heading', 'richtext', 'button']);
    expect(band.blocks[0]).toEqual({ kind: 'eyebrow', text: 'ROSART · BULGARIAN ROSE SKINCARE' });
    expect(band.blocks[3]).toEqual({ kind: 'button', label: 'Discover Our Story', href: '/our-story/' });
  });
});

describe('withFeaturedProducts', () => {
  it('places the featured products right after the collection tiles', () => {
    const { sections } = toSectionModel(template('index'), themeCtx, { firstHeadingIsH1: true });
    expect(kinds(withFeaturedProducts(sections))).toEqual([
      'hero', 'content', 'content', 'content', 'content', 'featured-products', 'content',
    ]);
  });

  it('appends them when there are no tiles', () => {
    expect(kinds(withFeaturedProducts([]))).toEqual(['featured-products']);
  });
});

describe('toSectionModel: Our Story', () => {
  const { sections, skipped } = toSectionModel(template('page.our-story'), themeCtx, { firstHeadingIsH1: false });

  it('skips the disabled page title section and the disabled heading block', () => {
    expect(kinds(sections)).toEqual(['hero', 'content', 'hero', 'content', 'content', 'hero', 'content', 'connections']);
    expect(blocksOf(sections[1]).map((b) => b.kind)).toEqual(['richtext']);
    expect(skipped).toEqual([]);
  });

  it('keeps the map hero with its mobile image', () => {
    const hero = sections[5];
    if (hero?.kind !== 'hero') throw new Error('no hero');
    expect(hero.image?.file).toBe('Rosart_Distillery_in_Bulgaria_s_Valley_of_Roses.png');
    expect(hero.mobileImage?.file).toBe('Rosart_s_Bulgarian_Valley_of_Roses_Map.png');
  });

  it('resolves the Explore Our Collection button', () => {
    const group = blocksOf(sections[6])[1];
    expect(group?.kind === 'group' && group.blocks.at(-1)).toEqual({
      kind: 'button',
      label: 'Explore Our Collection',
      href: '/our-collection/',
    });
  });

  it('reads the Global Connections block into structured content with local logos', () => {
    const connections = sections[7];
    if (connections?.kind !== 'connections') throw new Error('no connections');
    expect(connections.heading).toBe('Our Global Connections');
    expect(connections.intro).toMatch(/^Rooted in Bulgaria's Valley of Roses, Rosart's story continues/);
    expect(connections.items).toEqual([
      {
        role: 'Parent Company',
        name: 'Artec Chemical Group',
        logo: { file: 'ARTEC.png', alt: 'Artec Chemical Group' },
        link: { label: 'Visit Website', href: 'https://artechem.com/en/' },
      },
      {
        role: 'Brand Origin',
        name: 'Rosart · Bulgaria',
        logo: { file: 'ROSART.png', alt: 'Rosart Bulgaria' },
        link: { label: 'Visit Website', href: 'https://rosart-bg.com/home' },
      },
      {
        role: 'Australian Retail Partner',
        name: 'ORIZEN · Australia',
        logo: { file: 'ORIZEN.png', alt: 'ORIZEN Australia' },
        link: { label: 'Shop Rosart', href: 'https://orizen.com.au/' },
      },
    ]);
  });
});

describe('toSectionModel: Our Collection and 404', () => {
  it('shows the page title and the three collection cards in the theme order', () => {
    const { sections } = toSectionModel(template('page.our-collection'), themeCtx, { firstHeadingIsH1: false });
    expect(sections[0]).toEqual({ kind: 'page-title', id: 'main', showTitle: true, showBody: true });
    const list = sections[1];
    if (list?.kind !== 'collection-list') throw new Error('no collection list');
    expect(list.blocks).toEqual([{ kind: 'heading', text: 'Shop by collection', level: 2 }]);
    expect(list.collections.map((c) => c.url)).toEqual([
      '/collections/face-care/',
      '/collections/rose-water-toners/',
      '/collections/masks-treatments/',
    ]);
  });

  it('maps the 404 page copy and sends "all products" to Our Collection', () => {
    const { sections } = toSectionModel(template('404'), themeCtx, { firstHeadingIsH1: true });
    expect(kinds(sections)).toEqual(['content', 'product-list']);
    expect(blocksOf(sections[0])).toEqual([
      { kind: 'heading', text: 'Page not found', level: 1 },
      { kind: 'richtext', html: '<p>The link may be incorrect, or the page has been removed.</p>' },
      { kind: 'button', label: 'Continue shopping', href: '/our-collection/' },
    ]);
    expect(blocksOf(sections[1])).toEqual([{ kind: 'richtext', html: '<p>Discover something new</p>' }]);
  });
});

describe('toSectionModel: rules', () => {
  const section = (overrides: Partial<RawThemeSection>): RawThemeSection => ({
    id: 's',
    type: 'section',
    disabled: false,
    settings: {},
    blocks: [],
    ...overrides,
  });
  const text = (id: string, html: string, disabled = false) => ({ id, type: 'text', disabled, settings: { text: html }, blocks: [] });

  it('skips disabled blocks nested inside groups', () => {
    const { sections } = toSectionModel(
      {
        sections: [
          section({
            blocks: [{ id: 'g', type: 'group', disabled: false, settings: {}, blocks: [text('a', '<p>kept</p>'), text('b', '<p>gone</p>', true)] }],
          }),
        ],
      },
      themeCtx,
      { firstHeadingIsH1: false },
    );
    expect(blocksOf(sections[0])).toEqual([{ kind: 'group', blocks: [{ kind: 'richtext', html: '<p>kept</p>' }] }]);
  });

  it('reports what it cannot place instead of guessing', () => {
    const { sections, skipped } = toSectionModel(
      {
        sections: [
          section({ id: 'x', type: 'slideshow', blocks: [text('a', '<p>t</p>')] }),
          section({ id: 'y', type: 'custom-liquid', settings: { custom_liquid: '<div>{{ shop.name }}</div>' } }),
          section({ id: 'z', blocks: [{ id: 'b', type: 'button', disabled: false, settings: { label: 'Cart', link: '/cart' }, blocks: [] }] }),
        ],
      },
      themeCtx,
      { firstHeadingIsH1: false },
    );
    expect(sections).toEqual([]);
    expect(skipped).toEqual([
      'section x (slideshow): no site equivalent',
      'section y (custom-liquid): not a recognised layout',
      'button "Cart" in section z: link /cart has no site page',
    ]);
  });
});

describe('describeSections', () => {
  it('uses the first body text of the page, shortened for a meta description', () => {
    const { sections } = toSectionModel(template('page.our-story'), themeCtx, { firstHeadingIsH1: false });
    const description = describeSections(sections);
    expect(description).toMatch(/^From Our Fields, With Care\. Nestled in Bulgaria's renowned Valley of Roses/);
    expect(description.length).toBeLessThanOrEqual(160);
  });

  it('skips one-line body text such as the hero strapline', () => {
    const { sections } = toSectionModel(template('index'), themeCtx, { firstHeadingIsH1: true });
    expect(describeSections(sections)).toMatch(/^Born in Bulgaria's renowned Valley of Roses/);
  });

  it('returns an empty string when the page has no body text', () => {
    expect(describeSections([])).toBe('');
  });
});

describe('template extras', () => {
  it('reads the collection page band image', () => {
    expect(firstBackground(template('collection'), ctx.images)?.file).toBe('bg3.png');
    expect(firstBackground(template('page'), ctx.images)).toBeNull();
  });

  it('reads the product recommendations heading', () => {
    expect(recommendationsHeading(template('product.product-catalogue'))).toBe('You may also like');
    expect(recommendationsHeading(template('page'))).toBeNull();
  });

  it('reads the purchase note: the text block after the product template button, curly apostrophe intact', () => {
    expect(productPurchaseNote(template('product.product-catalogue'))).toBe(
      'You\u2019ll be redirected to ORIZEN, our Australian retail partner, to complete your purchase.',
    );
    expect(productPurchaseNote(template('page'))).toBeNull();
  });

  it('has no purchase note when the text after the button is disabled or not text', () => {
    const block = (type: string, settings: Record<string, unknown> = {}, disabled = false) => ({
      id: type, type, disabled, settings, blocks: [],
    });
    const product = (blocks: ReturnType<typeof block>[]) => ({
      sections: [{ id: 'main', type: 'product-information', disabled: false, settings: {}, blocks: [
        { ...block('_product-details'), blocks },
      ] }],
    });
    expect(productPurchaseNote(product([block('button', { label: 'SHOP NOW' }), block('text', { text: '<h6>Note</h6>' }, true)]))).toBeNull();
    expect(productPurchaseNote(product([block('button', { label: 'SHOP NOW' }), block('_divider')]))).toBeNull();
    expect(productPurchaseNote(product([block('button', { label: 'SHOP NOW' }, true), block('text', { text: '<p>Note</p>' })]))).toBeNull();
    expect(productPurchaseNote(product([block('button', { label: 'SHOP NOW' }), block('text', { text: '<p>Note</p>' })]))).toBe('Note');
  });
});

describe('siteChrome', () => {
  it('reads the announcement, footer text, logo and copyright switch', () => {
    expect(siteChrome(rawExport.theme, ctx)).toEqual({
      announcement: "From Bulgaria's Valley of Roses, with Care",
      footerText: 'Bulgarian Rose Skincare · Naturally Inspired',
      logo: { file: 'ESPRIT_HONG_KONG.png', alt: '' },
      showCopyright: true,
    });
  });
});

describe('toNavigation', () => {
  it('maps the main menu to site URLs', () => {
    expect(toNavigation(rawExport.menus, 'main-menu', ctx.urls)).toEqual([
      { title: 'Home', url: '/', children: [] },
      { title: 'Our Story', url: '/our-story/', children: [] },
      {
        title: 'Our Collection',
        url: '/our-collection/',
        children: [
          { title: 'Face Care', url: '/collections/face-care/', children: [] },
          { title: 'Rose Water & Toners', url: '/collections/rose-water-toners/', children: [] },
          { title: 'Masks & Treatments', url: '/collections/masks-treatments/', children: [] },
        ],
      },
      { title: 'Contact', url: '/contact/', children: [] },
    ]);
  });

  it('drops footer links with no site page (search, privacy choices)', () => {
    expect(toNavigation(rawExport.menus, 'footer', ctx.urls)).toEqual([]);
  });
});
