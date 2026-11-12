import { describe, expect, it } from 'vitest';
import {
  ALLOWED_DIFFERENCES,
  compareOutlines,
  expectedOutline,
  renderedOutline,
  type FidelityData,
  type OutlineNode,
} from './fidelity.ts';
import { createImageResolver } from './images.ts';
import type { RawProduct, RawThemeBlock, RawThemeSection } from './shopify-types.ts';
import { createUrlMap } from './urls.ts';

const product = (handle: string, title: string, image: string): RawProduct => ({
  handle,
  title,
  status: 'ACTIVE',
  descriptionHtml: `<p>About ${title}</p>`,
  productType: '',
  vendor: 'Rosart',
  tags: [],
  seo: { title: null, description: null },
  images: [{ altText: null, url: `https://cdn.example/${image}`, width: 10, height: 10 }],
  metafields: [{ namespace: 'custom', key: 'how_to_use', type: 'single_line_text_field', value: 'Apply daily.' }],
  variants: [{ price: '10.00', sku: null, compareAtPrice: null }],
});

const products = [product('a', 'Rose A', 'a.png'), product('b', 'Rose B', 'b.png'), product('c', 'Rose C', 'c.png')];
const data: FidelityData = {
  urls: createUrlMap({
    shopDomains: ['shop.example'],
    pages: [{ handle: 'our-story', title: 'Our Story' }, { handle: 'our-collection', title: 'Our Collection' }],
    collections: [{ handle: 'face', title: 'Face' }],
    products,
  }),
  images: createImageResolver(
    {
      images: {},
      shop_images: { 'bg.png': 'bg.png', 'logo.png': 'logo.png' },
      sources: { 'https://cdn.example/a.png': 'a.png', 'https://cdn.example/b.png': 'b.png', 'https://cdn.example/c.png': 'c.png' },
    },
    [],
    {},
  ),
  products,
  collections: [{ handle: 'face', title: 'Face', descriptionHtml: '', image: null, productHandles: ['b', 'a'], seo: { title: null, description: null } }],
  menus: [],
  shopName: 'Rosart',
  logo: 'shopify://shop_images/logo.png',
  year: 2026,
};

const block = (id: string, type: string, settings: Record<string, unknown> = {}, extra: Partial<RawThemeBlock> = {}): RawThemeBlock => ({
  id,
  type,
  disabled: false,
  settings,
  blocks: [],
  ...extra,
});
const section = (id: string, type: string, blocks: RawThemeBlock[] = [], extra: Partial<RawThemeSection> = {}): RawThemeSection => ({
  id,
  type,
  disabled: false,
  settings: {},
  blocks,
  ...extra,
});
const node = (id: string, type: string, extra: Partial<OutlineNode> = {}): OutlineNode => ({
  id,
  type,
  text: '',
  images: [],
  links: [],
  children: [],
  ...extra,
});

describe('expectedOutline', () => {
  const template = 'templates/test.json';

  it('keeps enabled sections and blocks in theme order and drops disabled ones', () => {
    const outline = expectedOutline(
      [
        section('hero', 'hero', [block('t1', 'text', { text: '<h1>Hello</h1>' }), block('b1', 'button', { label: 'Go', link: '/' }, { disabled: true })], {
          settings: { image_1: 'shopify://shop_images/bg.png' },
        }),
        section('off', 'section', [block('t2', 'text', { text: '<p>x</p>' })], { disabled: true }),
        section('empty', 'section'),
      ],
      { template },
      data,
    );
    expect(outline).toEqual([
      node('hero', 'hero', { images: ['bg.png'], children: [node('t1', 'text', { text: 'Hello' })] }),
      node('empty', 'section'),
    ]);
  });

  it('resolves a Liquid binding to the source it names, and refuses one it does not know', () => {
    const titled = [section('main', 'section', [block('t', 'text', {}, { bindings: { text: '<h1>{{ closest.product.title }}</h1>' } })])];
    expect(expectedOutline(titled, { template, product: products[0] }, data)[0]?.children).toEqual([node('t', 'text', { text: 'Rose A' })]);
    const unknown = [section('main', 'section', [block('t', 'text', {}, { bindings: { text: '{{ closest.product.vendor }}' } })])];
    expect(() => expectedOutline(unknown, { template, product: products[0] }, data)).toThrow(/closest\.product\.vendor/);
  });

  it('fails on a block type it has no rule for, instead of guessing', () => {
    expect(() => expectedOutline([section('s', 'section', [block('x', 'slideshow')])], { template }, data)).toThrow(/slideshow/);
  });

  it('expands a product list into one card per product of the collection it names, up to its limit', () => {
    const card = block('card', '_product-card', {}, { static: true, blocks: [block('g', '_product-card-gallery'), block('t', 'product-title'), block('p', 'price')] });
    const outline = expectedOutline(
      [section('list', 'product-list', [block('head', '_product-list-content', {}, { static: true, blocks: [block('h', 'text', { text: '<p>New</p>' })] }), card], {
        settings: { collection: 'face', max_products: 1 },
      })],
      { template },
      data,
    );
    // Price is an allowed difference (the site shows no prices), so it is absent.
    expect(outline[0]?.children).toEqual([
      node('head', '_product-list-content', { children: [node('h', 'text', { text: 'New' })] }),
      node('card', '_product-card', {
        links: ['/products/b/'],
        children: [node('g', '_product-card-gallery', { images: ['b.png'] }), node('t', 'product-title', { text: 'Rose B' })],
      }),
    ]);
  });

  it('expects the owner label on the ORIZEN button, and the product URL the theme binds when there is one', () => {
    const template = 'templates/product.product-catalogue.json';
    const button = block('button_z9EEwn', 'button', { label: 'SHOP NOW' }, { bindings: { link: '{{ closest.product.metafields.custom.orizen_product_url.value }}' } });
    const details = [section('main', 'product-information', [block('product-details', '_product-details', {}, { static: true, blocks: [button] })])];
    const withUrl = { ...products[0]!, metafields: [{ namespace: 'custom', key: 'orizen_product_url', type: 'url', value: 'https://orizen.com.au/products/a' }] };
    const nodeOf = (product: RawProduct) => expectedOutline(details, { template, product }, data)[0]?.children[0]?.children[0];
    expect(nodeOf(withUrl)).toEqual(node('button_z9EEwn', 'button', { text: 'Shop in Orizen.com.au', links: ['https://orizen.com.au/products/a'] }));
    // No product URL: the site links to the ORIZEN search (owner decision), so the link is not compared.
    expect(nodeOf(products[1]!)).toEqual(node('button_z9EEwn', 'button', { text: 'Shop in Orizen.com.au', links: ['*'] }));
    // A product URL that is not https on orizen.com.au is not used either: the site falls back to the search.
    const elsewhere = { ...withUrl, metafields: [{ namespace: 'custom', key: 'orizen_product_url', type: 'url', value: 'https://example.com/a' }] };
    expect(nodeOf(elsewhere)?.links).toEqual(['*']);
    expect(ALLOWED_DIFFERENCES['shop-now-label'].reason).toMatch(/owner decision 2026-11-07/i);
  });

  it('every allowed difference carries a reason', () => {
    for (const entry of Object.values(ALLOWED_DIFFERENCES)) expect(entry.reason.length).toBeGreaterThan(20);
  });
});

describe('renderedOutline', () => {
  const html = (body: string) => `<!doctype html><html><body>${body}</body></html>`;

  it('reads sections and blocks from data attributes, with the text, images and links each one owns', () => {
    const outline = renderedOutline(
      html(`
      <a data-allowed="skip-link" href="#main">Skip to content</a>
      <section data-theme-section="hero" data-theme-type="hero">
        <picture><source data-image="m.png"><img data-image="bg.png" src="/_astro/bg.webp" alt=""></picture>
        <div data-theme-block="t1" data-theme-type="text"><h1>Hello <em>there</em></h1></div>
        <a data-theme-block="b1" data-theme-type="button" href="/our-story/">Go</a>
      </section>`),
    );
    expect(outline.problems).toEqual([]);
    expect(outline.nodes).toEqual([
      node('hero', 'hero', {
        images: ['m.png', 'bg.png'],
        children: [node('t1', 'text', { text: 'Hello there' }), node('b1', 'button', { text: 'Go', links: ['/our-story/'] })],
      }),
    ]);
  });

  it('reports content outside any theme section, and unknown allowance keys', () => {
    const outline = renderedOutline(html('<main><p>Extra words</p><span data-allowed="made-up">x</span></main>'));
    expect(outline.problems).toEqual(['content outside any theme section: text "Extra words"', 'data-allowed="made-up" is not in ALLOWED_DIFFERENCES']);
  });
});

describe('compareOutlines', () => {
  const expected = [node('s1', 'section', { children: [node('t', 'text', { text: 'Hi' })] }), node('s2', 'section')];

  it('passes when the site renders exactly the theme outline (whitespace aside)', () => {
    expect(compareOutlines(expected, [node('s1', 'section', { children: [node('t', 'text', { text: ' Hi ' })] }), node('s2', 'section')])).toEqual([]);
  });

  it('names extra, missing and different nodes', () => {
    const problems = compareOutlines(expected, [
      node('s1', 'section', { children: [node('t', 'text', { text: 'Bye' })] }),
      node('featured', 'product-list'),
    ]);
    expect(problems).toEqual([
      'theme s1 > t [text] text "Hi", site has text "Bye"',
      'site renders featured [product-list] after s1, the theme has no such node here',
      'theme s2 [section] is missing on the site',
    ]);
  });
});

describe('links inside rich text', () => {
  const template = 'templates/test.json';
  const body = '<p>Email: <a href="mailto:info@rosart.com.au"><span>info@rosart.com.au</span></a> or read <a href="https://shop.example/pages/our-story">our story</a>.</p>';
  const expectedLinks = (html: string, type = 'text') =>
    expectedOutline([section('s', 'section', [block('t', type, { text: html })])], { template }, data)[0]?.children[0]?.links;

  it('expects the links the Shopify HTML carries, resolved the way the site resolves them', () => {
    expect(expectedLinks(body)).toEqual(['mailto:info@rosart.com.au', '/our-story/']);
    expect(expectedLinks(body, '_announcement')).toEqual(['mailto:info@rosart.com.au', '/our-story/']);
  });

  it('expects the links of a page body bound to the page content', () => {
    const page = { handle: 'contact', title: 'Contact', bodyHtml: body, isPublished: true, seo: { title: null, description: null } };
    const bound = [section('s', 'main-page', [block('c', 'text', {}, { bindings: { text: '{{ closest.page.content }}' } })])];
    expect(expectedOutline(bound, { template, page }, data)[0]?.children[0]?.links).toEqual(['mailto:info@rosart.com.au', '/our-story/']);
  });

  it('expects no link for one the site policy refuses (javascript:, an unknown protocol) or a link with no text', () => {
    expect(expectedLinks('<p><a href="javascript:alert(1)">bad</a> <a href="ftp://x/y">ftp</a> <a href="/p/">  </a>ok</p>')).toEqual([]);
  });

  it('passes a site that renders those links, and fails one that adds or drops a link', () => {
    const expected = expectedOutline([section('s', 'section', [block('t', 'text', { text: body })])], { template }, data);
    const site = (links: string[]) => [node('s', 'section', { children: [node('t', 'text', { text: 'Email: info@rosart.com.au or read our story.', links })] })];
    expect(compareOutlines(expected, site(['mailto:info@rosart.com.au', '/our-story/']))).toEqual([]);
    expect(compareOutlines(expected, site(['mailto:info@rosart.com.au', '/our-story/', '/extra/']))).toHaveLength(1);
    expect(compareOutlines(expected, site(['mailto:info@rosart.com.au']))).toHaveLength(1);
  });

  it('still fails a site link the content does not carry', () => {
    const plain = expectedOutline([section('s', 'section', [block('t', 'text', { text: '<p>Email: info@rosart.com.au</p>' })])], { template }, data);
    const withLink = [node('s', 'section', { children: [node('t', 'text', { text: 'Email: info@rosart.com.au', links: ['mailto:info@rosart.com.au'] })] })];
    expect(compareOutlines(plain, withLink)).toHaveLength(1);
  });
});
