import { describe, expect, it, vi } from 'vitest';
import { toCollections } from './catalog.ts';
import { createSiteContext, rawExport } from './export-data.ts';
import type { RawThemeSection } from './shopify-types.ts';
import {
  describeSections,
  productDetailSections,
  siteChrome,
  toNavigation,
  toSectionModel,
  type Block,
  type ModelOptions,
  type Section,
} from './theme.ts';

// Exact-value tests run on the frozen snapshot, never on the live export (tests/fixtures/export-snapshot).
vi.mock('./export-data.ts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./export-data.ts')>();
  const { fixtureExport } = await import('../../tests/fixtures/export-snapshot/load.ts');
  return { ...actual, rawExport: fixtureExport };
});

const ctx = createSiteContext(rawExport, { imageAlt: {} });
const themeCtx = {
  ...ctx,
  siteCollections: toCollections(rawExport.collections, ctx),
  allProductHandles: ctx.products.map((p) => p.handle),
};
const template = (name: string) => {
  const found = rawExport.theme.templates[`templates/${name}.json`];
  if (!found) throw new Error(name);
  return found;
};
const model = (name: string, options: Partial<ModelOptions> = {}) =>
  toSectionModel(template(name), themeCtx, { firstHeadingIsH1: true, template: `templates/${name}.json`, ...options });
const page = (handle: string) => {
  const found = rawExport.pages.find((p) => p.handle === handle);
  if (!found) throw new Error(handle);
  return found;
};
const product = (handle: string) => {
  const found = rawExport.products.find((p) => p.handle === handle);
  if (!found) throw new Error(handle);
  return found;
};
const kinds = (sections: Section[]) => sections.map((s) => s.kind);
const blocksOf = (section: Section | undefined): Block[] => (section && 'blocks' in section ? section.blocks : []);

describe('toSectionModel: homepage', () => {
  const { sections, skipped } = model('index');

  it('keeps every enabled section in theme order, the empty one as a spacer', () => {
    expect(sections.map((s) => `${s.id} ${s.kind}`)).toEqual([
      'hero_jVaWmY hero',
      'section_hQaLzP content',
      'section_mKDAGQ content',
      'section_n4eLBK content',
      'section_t4EcwA content',
      'section_kFXkyX spacer',
      'section_GPKnxM content',
    ]);
    expect(skipped).toEqual([]);
  });

  it('builds the hero with desktop and mobile images, the h1, and no disabled button', () => {
    const hero = sections[0];
    if (hero?.kind !== 'hero') throw new Error('no hero');
    expect(hero.image?.file).toBe('Untitled_design_7_f6c26f2b-ad40-440e-a57a-fb6da4fe0dcb.png');
    expect(hero.mobileImage?.file).toBe('DSC08666_Rosart_Mobile_1200x1500_e230edb1-8068-47e5-b216-efa8170e4f7e.jpg');
    expect(hero.blocks).toEqual([
      { id: 'text_YLPk4p', type: 'text', kind: 'heading', text: 'From Soil to Skin', level: 1 },
      { id: 'text_DUhktp', type: 'text', kind: 'richtext', html: '<p>From Our Rose Fields to Your Skin.</p>' },
    ]);
  });

  it('turns later headings into h2 and keeps body text', () => {
    expect(blocksOf(sections[1])[0]).toEqual({ id: 'text_KWpnqx', type: 'text', kind: 'heading', text: 'Rooted in the Valley of Roses', level: 2 });
    expect(blocksOf(sections[1])[1]).toMatchObject({ kind: 'richtext' });
  });

  it('keeps the image and text group, and the certifications image on its background', () => {
    expect(blocksOf(sections[2]).map((b) => b.kind)).toEqual(['image', 'group']);
    const certifications = sections[3];
    if (certifications?.kind !== 'content') throw new Error('no certifications section');
    expect(certifications.background?.file).toBe('bg1.png');
    expect(certifications.blocks[0]).toMatchObject({ kind: 'image', image: { file: 'USDA_and_EU_Organic_Certifications.png' } });
  });

  it('keeps the three collection tiles unlinked, as the theme has no link on them', () => {
    const tiles = blocksOf(sections[4]);
    expect(tiles.map((b) => [b.id, b.kind, b.kind === 'group' ? b.href : undefined])).toEqual([
      ['group_DnPwbh', 'group', undefined],
      ['group_4B9m9J', 'group', undefined],
      ['group_NHAVXA', 'group', undefined],
    ]);
  });

  it('builds the closing band with eyebrow, heading and the Our Story button', () => {
    const band = sections[6];
    if (band?.kind !== 'content') throw new Error('no band');
    expect(band.background?.file).toBe('bg3.png');
    expect(band.blocks.map((b) => b.kind)).toEqual(['eyebrow', 'heading', 'richtext', 'button']);
    expect(band.blocks[0]).toEqual({ id: 'text_zf64fr', type: 'text', kind: 'eyebrow', text: 'ROSART · BULGARIAN ROSE SKINCARE' });
    expect(band.blocks[3]).toEqual({ id: 'button_3zVejn', type: 'button', kind: 'button', label: 'Discover Our Story', href: '/our-story/' });
  });
});

describe('toSectionModel: Our Story', () => {
  const { sections, skipped } = model('page.our-story', { firstHeadingIsH1: false, scope: { page: page('our-story') } });

  it('skips the disabled page title section and the disabled heading block', () => {
    expect(kinds(sections)).toEqual(['hero', 'content', 'hero', 'content', 'content', 'hero', 'content', 'connections']);
    expect(blocksOf(sections[1]).map((b) => b.kind)).toEqual(['richtext']);
    expect(skipped).toEqual([]);
  });

  it('keeps the theme heading level below the h1 (an h3 stays an h3)', () => {
    expect(blocksOf(sections[4])[0]).toMatchObject({ kind: 'heading', text: 'FROM NATURE TO FORMULATION', level: 3 });
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
      id: 'button_TKgfF7',
      type: 'button',
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
    expect(connections.items.map((i) => [i.role, i.name, i.logo.file, i.link?.href])).toEqual([
      ['Parent Company', 'Artec Chemical Group', 'ARTEC.png', 'https://artechem.com/en/'],
      ['Brand Origin', 'Rosart · Bulgaria', 'ROSART.png', 'https://rosart-bg.com/home'],
      ['Australian Retail Partner', 'ORIZEN · Australia', 'ORIZEN.png', 'https://orizen.com.au/'],
    ]);
  });
});

describe('toSectionModel: Our Collection, contact and 404', () => {
  it('shows the page title the theme binds ({{ closest.page.title }}), then the collection cards in theme order', () => {
    const { sections } = model('page.our-collection', { scope: { page: page('our-collection') } });
    expect(sections[0]).toEqual({
      id: 'main',
      type: 'main-page',
      kind: 'content',
      background: null,
      blocks: [{ id: 'heading', type: 'text', kind: 'heading', text: 'Our Collection', level: 1 }],
    });
    const list = sections[1];
    if (list?.kind !== 'collection-list') throw new Error('no collection list');
    expect(list.before).toEqual([
      { id: 'group_j6ne7b', type: 'group', kind: 'group', blocks: [{ id: 'text_gzcb9h', type: 'text', kind: 'heading', text: 'Shop by collection', level: 3 }] },
    ]);
    expect(list.card).toEqual({
      id: 'static-collection-card',
      type: '_collection-card',
      blocks: [
        { id: 'collection-card-image', type: '_collection-card-image' },
        { id: 'collection_title_wPhi9K', type: 'collection-title' },
      ],
    });
    expect(list.collections.map((c) => c.url)).toEqual([
      '/collections/face-care/',
      '/collections/rose-water-toners/',
      '/collections/masks-treatments/',
    ]);
  });

  it('builds the contact page from its template: bound title and body, then the form substitute', () => {
    const { sections } = model('page.contact', { scope: { page: page('contact') } });
    expect(sections.map((s) => `${s.id} ${s.kind}`)).toEqual(['main content', 'form content']);
    expect(blocksOf(sections[0]).map((b) => `${b.id} ${b.kind}`)).toEqual(['title heading', 'content richtext']);
    expect(blocksOf(sections[1])).toEqual([{ id: 'contact_form_UwiCkQ', type: 'contact-form', kind: 'contact-form' }]);
  });

  it('maps the 404 copy and lists the first 4 products of the `all` collection the theme names', () => {
    const { sections } = model('404');
    expect(kinds(sections)).toEqual(['content', 'product-list']);
    expect(blocksOf(sections[0]).map((b) => b.kind)).toEqual(['heading', 'richtext', 'button']);
    expect(blocksOf(sections[0])[2]).toMatchObject({ label: 'Continue shopping', href: '/our-collection/' });
    const list = sections[1];
    if (list?.kind !== 'product-list') throw new Error('no product list');
    expect(list.before).toEqual([
      {
        id: 'static-header',
        type: '_product-list-content',
        kind: 'group',
        blocks: [{ id: 'text_WJEa9Q', type: 'text', kind: 'richtext', html: '<p>Discover something new</p>' }],
      },
    ]);
    // Price is an allowed difference: the card shows the image and the title.
    expect(list.card?.blocks.map((b) => b.type)).toEqual(['_product-card-gallery', 'product-title']);
    expect(list.productHandles).toEqual(ctx.products.slice(0, 4).map((p) => p.handle));
  });
});

describe('toSectionModel: collection and product templates', () => {
  it('binds the collection band to the collection title and description, then lists its products', () => {
    const collection = rawExport.collections.find((c) => c.handle === 'face-care-copy');
    const { sections } = model('collection', { scope: { collection } });
    const band = sections[0];
    if (band?.kind !== 'content') throw new Error('no band');
    expect(band.background?.file).toBe('bg3.png');
    expect(band.blocks[0]).toMatchObject({ id: 'text_tqQTNE', kind: 'heading', text: 'Rose Water & Toners', level: 1 });
    const grid = sections[1];
    if (grid?.kind !== 'product-list') throw new Error('no grid');
    expect(grid.productHandles).toEqual(collection?.productHandles);
  });

  it('renders the product details in theme order with the bound title, description and metafields', () => {
    const { sections, skipped } = model('product.product-catalogue', { scope: { product: product('rose-face-cream-50ml') } });
    expect(skipped).toEqual([]);
    expect(kinds(sections)).toEqual(['product', 'recommendations']);
    const [gallery, details] = blocksOf(sections[0]);
    expect(gallery).toMatchObject({ id: 'media-gallery', kind: 'media-gallery' });
    if (details?.kind !== 'group') throw new Error('no details');
    expect(details.blocks.map((b) => `${b.id} ${b.kind}`)).toEqual([
      'group_icgrde group',
      'text_aEtTtq richtext',
      'accordion_Ygf6jp group',
      'button_z9EEwn button',
      'text_pY4eBT heading',
      'divider_VJhene divider',
    ]);
    expect(details.blocks[0]).toMatchObject({ blocks: [{ kind: 'heading', text: 'Rose Face Cream 50ml', level: 1 }] });
    const accordion = details.blocks[2];
    if (accordion?.kind !== 'group') throw new Error('no accordion');
    expect(accordion.blocks.map((r) => (r.kind === 'accordion-row' ? r.heading : r.kind))).toEqual([
      'HOW TO USE',
      'THE INGREDIENTS',
      'CERTIFICATIONS & BRAND VALUES',
      'CAUTION',
    ]);
    // Owner decision 2026-11-07: the label is "Shop in Orizen.com.au", not the theme's "SHOP NOW".
    expect(details.blocks[3]).toMatchObject({
      label: 'Shop in Orizen.com.au',
      external: true,
      href: expect.stringMatching(/^https:\/\/orizen\.com\.au\/search\?q=/),
    });
    expect(blocksOf(sections[1])).toEqual([{ id: 'text_cbcgyb', type: 'text', kind: 'heading', text: 'You may also like', level: 3 }]);
  });

  it('links the ORIZEN button to the custom.orizen_product_url the theme binds, only when it is https on orizen.com.au', () => {
    const raw = product('rose-face-cream-50ml');
    const withUrl = (value: string) => ({
      ...raw,
      metafields: [...raw.metafields, { namespace: 'custom', key: 'orizen_product_url', type: 'url', value }],
    });
    const button = (value: string) => {
      const { sections } = model('product.product-catalogue', { scope: { product: withUrl(value) } });
      const details = blocksOf(sections[0])[1];
      return details?.kind === 'group' ? details.blocks.find((b) => b.kind === 'button') : undefined;
    };
    expect(button('https://orizen.com.au/products/rose-face-cream-50ml')).toMatchObject({
      label: 'Shop in Orizen.com.au',
      href: 'https://orizen.com.au/products/rose-face-cream-50ml',
    });
    expect(button('https://www.orizen.com.au/products/x')).toMatchObject({ href: 'https://www.orizen.com.au/products/x' });
    for (const other of ['https://example.com/rose', 'http://orizen.com.au/products/x', 'https://orizen.com.au.evil.example/x']) {
      expect(button(other)).toMatchObject({ href: expect.stringMatching(/^https:\/\/orizen\.com\.au\/search\?q=Rose%20Face%20Cream%2050ml$/) });
    }
  });

  it('reads the detail metafields from the accordion bindings, not from the headings', () => {
    expect(productDetailSections(template('product.product-catalogue'))).toEqual([
      { key: 'how_to_use', heading: 'HOW TO USE' },
      { key: 'the_ingredients', heading: 'THE INGREDIENTS' },
      { key: 'certifications_brand_values', heading: 'CERTIFICATIONS & BRAND VALUES' },
      { key: 'caution', heading: 'CAUTION' },
    ]);
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
  const options = { firstHeadingIsH1: false, template: 'templates/test.json' };

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
      options,
    );
    expect(blocksOf(sections[0])).toEqual([
      { id: 'g', type: 'group', kind: 'group', blocks: [{ id: 'a', type: 'text', kind: 'richtext', html: '<p>kept</p>' }] },
    ]);
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
      options,
    );
    expect(sections).toEqual([{ id: 'z', type: 'section', kind: 'spacer' }]);
    expect(skipped).toEqual([
      'section x (slideshow): no site equivalent',
      'section y (custom-liquid): not a recognised layout',
      'button "Cart" in section z: link /cart has no site page',
    ]);
  });
});

describe('describeSections', () => {
  it('uses the first body text of the page, shortened for a meta description', () => {
    const { sections } = model('page.our-story', { firstHeadingIsH1: false, scope: { page: page('our-story') } });
    const description = describeSections(sections);
    expect(description).toMatch(/^From Our Fields, With Care\. Nestled in Bulgaria's renowned Valley of Roses/);
    expect(description.length).toBeLessThanOrEqual(160);
  });

  it('skips one-line body text such as the hero strapline', () => {
    expect(describeSections(model('index').sections)).toMatch(/^Born in Bulgaria's renowned Valley of Roses/);
  });

  it('returns an empty string when the page has no body text', () => {
    expect(describeSections([])).toBe('');
  });
});

describe('siteChrome', () => {
  it('reads the header and footer groups in theme order, without the allowed absences', () => {
    const chrome = siteChrome(rawExport.theme, ctx);
    expect(chrome.header.map((s) => [s.id, s.type, s.blocks.map((b) => `${b.id} ${b.type}`)])).toEqual([
      ['header_announcements_9jGBFp', 'header-announcements', ['announcement_BxgCk9 _announcement']],
      ['header_section', 'header', ['header-logo _header-logo', 'header-menu _header-menu']],
    ]);
    expect(chrome.header[0]?.blocks[0]?.html).toContain('From Bulgaria');
    // Policy links and the placeholder social links are allowed differences.
    expect(chrome.footer).toEqual([
      { id: 'footer_m9NzUG', type: 'footer', blocks: [{ id: 'text_dGnm6k', type: 'text', html: '<p>Bulgarian Rose Skincare · Naturally Inspired</p>' }] },
      { id: 'footer_utilities_jLGE8U', type: 'footer-utilities', blocks: [{ id: 'footer_copyright_jweRK8', type: 'footer-copyright' }] },
    ]);
    // The logo's alt is the one Shopify holds for that file in files.json.
    const logoFile = rawExport.files.find((f) => {
      const url = new URL(f.url);
      return rawExport.manifest.sources[`${url.origin}${url.pathname}`] === 'ESPRIT_HONG_KONG.png';
    });
    expect(logoFile?.alt).toBeTruthy();
    expect(chrome.logo).toEqual({ file: 'ESPRIT_HONG_KONG.png', alt: logoFile?.alt });
    expect(chrome.footerText).toBe('Bulgarian Rose Skincare · Naturally Inspired');
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
