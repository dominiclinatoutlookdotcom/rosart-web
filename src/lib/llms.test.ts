import { describe, expect, it } from 'vitest';
import { htmlToText } from './html.ts';
import { buildLlmsTxt, productSummary, wholeSentences, type LlmsTxtInput } from './llms.ts';
import { truncate } from './text.ts';

const input = (overrides: Partial<LlmsTxtInput> = {}): LlmsTxtInput => ({
  siteUrl: 'https://rosart.com.au',
  name: 'Rosart',
  summary: 'Organic Bulgarian rose skincare.',
  pages: [
    { title: 'Our Story', url: '/our-story/', summary: 'From our fields.' },
    { title: 'Contact us', url: '/contact/', summary: 'Get in touch.' },
  ],
  collections: [{ title: 'Face Care', url: '/collections/face-care/', summary: 'Face Care by Rosart.' }],
  products: [{ title: 'Rose Water 100ml', url: '/products/rose-water-100ml/', summary: 'Pure rose water.' }],
  retailer: { name: 'ORIZEN', url: 'https://orizen.com.au/' },
  enquiryEmail: 'info@rosart.com.au',
  ...overrides,
});

describe('buildLlmsTxt', () => {
  it('writes the title, summary and sections with absolute links, in order', () => {
    expect(buildLlmsTxt(input())).toBe(
      [
        '# Rosart',
        '',
        '> Organic Bulgarian rose skincare.',
        '',
        '## Pages',
        '',
        '- [Our Story](https://rosart.com.au/our-story/): From our fields.',
        '- [Contact us](https://rosart.com.au/contact/): Get in touch.',
        '',
        '## Collections',
        '',
        '- [Face Care](https://rosart.com.au/collections/face-care/): Face Care by Rosart.',
        '',
        '## Products',
        '',
        '- [Rose Water 100ml](https://rosart.com.au/products/rose-water-100ml/): Pure rose water.',
        '',
        '## Where to buy',
        '',
        'Buy Rosart products from [ORIZEN](https://orizen.com.au/), the Australian retail partner. Enquiries: info@rosart.com.au',
        '',
      ].join('\n'),
    );
  });

  it('builds links from the configured site URL', () => {
    const text = buildLlmsTxt(input({ siteUrl: 'https://staging.example.com' }));
    expect(text).toContain('- [Our Story](https://staging.example.com/our-story/): From our fields.');
    expect(text).not.toContain('rosart.com.au/');
  });

  it('keeps every entry and the summary on one line', () => {
    const text = buildLlmsTxt(
      input({
        summary: 'Line one.\n\nLine   two.',
        products: [{ title: 'Rose\nWater', url: '/products/x/', summary: 'Soothes.\r\n Hydrates.' }],
      }),
    );
    expect(text).toContain('> Line one. Line two.\n');
    expect(text).toContain('- [Rose Water](https://rosart.com.au/products/x/): Soothes. Hydrates.\n');
  });

  it('escapes brackets in link text so a title cannot break the link', () => {
    const text = buildLlmsTxt(input({ products: [{ title: 'Cream [3g]', url: '/products/c/', summary: 'Rich.' }] }));
    expect(text).toContain('- [Cream \\[3g\\]](https://rosart.com.au/products/c/): Rich.');
  });

  it('leaves out the summary text and colon when an entry has none', () => {
    const text = buildLlmsTxt(input({ products: [{ title: 'Soap', url: '/products/soap/', summary: '  ' }] }));
    expect(text).toContain('- [Soap](https://rosart.com.au/products/soap/)\n');
  });

  it('leaves out an empty section', () => {
    expect(buildLlmsTxt(input({ collections: [] }))).not.toContain('## Collections');
  });

  it('is plain Markdown with no HTML tags', () => {
    const text = buildLlmsTxt(input({ products: [{ title: 'A & B', url: '/products/ab/', summary: 'Rose & oil.' }] }));
    expect(text).not.toMatch(/<[a-z/]/i);
    expect(text).not.toContain('&amp;');
  });
});

describe('wholeSentences', () => {
  it('drops the unfinished sentence a truncated description ends with', () => {
    expect(wholeSentences('Born in the Valley of Roses. From freshly harvested petals to…')).toBe(
      'Born in the Valley of Roses.',
    );
  });

  it('keeps complete text as it is', () => {
    expect(wholeSentences('One. Two!')).toBe('One. Two!');
  });

  it('keeps truncated text that has no complete sentence', () => {
    expect(wholeSentences('Face Care by Rosart: a, b, c…')).toBe('Face Care by Rosart: a, b, c…');
  });
});

describe('productSummary', () => {
  it('uses the SEO description written in Shopify', () => {
    expect(
      productSummary({ seo: { description: 'Discover rose water.' }, descriptionHtml: '<p>Long text. More.</p>' }),
    ).toBe('Discover rose water.');
  });

  it('uses the first sentence of the description when Shopify has no SEO description', () => {
    const descriptionHtml = `<p>A gentle cream for daily use. ${'Word '.repeat(60)}</p>`;
    // The catalog fills a missing SEO description with the truncated description text.
    const derived = truncate(htmlToText(descriptionHtml));
    expect(productSummary({ seo: { description: derived }, descriptionHtml })).toBe('A gentle cream for daily use.');
    expect(productSummary({ seo: { description: '' }, descriptionHtml })).toBe('A gentle cream for daily use.');
  });
});
