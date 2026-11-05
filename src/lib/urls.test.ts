import { describe, expect, it } from 'vitest';
import { createUrlMap, slugify } from './urls.ts';

const input = {
  shopDomains: ['rosart-mhfmyuxq.myshopify.com'],
  pages: [
    { handle: 'contact', title: 'Contact us' },
    { handle: 'our-story', title: 'Our Story' },
    { handle: 'our-collection', title: 'Our Collection' },
    { handle: 'data-sharing-opt-out', title: 'Your Privacy Choices' },
  ],
  collections: [
    { handle: 'face-care', title: 'Face Care' },
    { handle: 'face-care-copy', title: 'Rose Water & Toners' },
    { handle: 'rose-water-toners-copy', title: 'Masks & Treatments' },
    { handle: 'frontpage', title: 'Home page' },
  ],
  products: [{ handle: 'rose-face-cream-3g' }, { handle: 'rose-water-100ml' }],
};

describe('slugify', () => {
  it('drops ampersands and punctuation, and folds accents', () => {
    expect(slugify('Rose Water & Toners')).toBe('rose-water-toners');
    expect(slugify('Masks & Treatments')).toBe('masks-treatments');
    expect(slugify('  Rose Face Soufflé Cream!  ')).toBe('rose-face-souffle-cream');
  });
});

describe('createUrlMap', () => {
  const map = createUrlMap(input);

  it('gives collections clean URLs from their titles, not their Shopify handles', () => {
    expect(map.collection('face-care')).toBe('/collections/face-care/');
    expect(map.collection('face-care-copy')).toBe('/collections/rose-water-toners/');
    expect(map.collection('rose-water-toners-copy')).toBe('/collections/masks-treatments/');
  });

  it('sends the homepage collection to the homepage', () => {
    expect(map.collection('frontpage')).toBe('/');
  });

  it('keeps page and product handles, with a trailing slash', () => {
    expect(map.page('our-story')).toBe('/our-story/');
    expect(map.page('contact')).toBe('/contact/');
    expect(map.product('rose-water-100ml')).toBe('/products/rose-water-100ml/');
  });

  it('has no URL for the Shopify privacy-choices page or unknown handles', () => {
    expect(map.page('data-sharing-opt-out')).toBeUndefined();
    expect(map.page('nope')).toBeUndefined();
    expect(map.collection('nope')).toBeUndefined();
    expect(map.product('nope')).toBeUndefined();
  });

  it.each([
    ['/', '/'],
    ['shopify://pages/our-story', '/our-story/'],
    ['/pages/our-collection', '/our-collection/'],
    ['shopify://collections/face-care-copy', '/collections/rose-water-toners/'],
    ['/collections/rose-water-toners-copy', '/collections/masks-treatments/'],
    ['shopify://collections/all', '/our-collection/'],
    ['/collections/all', '/our-collection/'],
    ['/collections', '/our-collection/'],
    ['shopify://products/rose-face-cream-3g', '/products/rose-face-cream-3g/'],
    ['/products/rose-face-cream-3g', '/products/rose-face-cream-3g/'],
    ['/collections/face-care/products/rose-face-cream-3g', '/products/rose-face-cream-3g/'],
    ['/pages/contact#form', '/contact/#form'],
    ['https://rosart-mhfmyuxq.myshopify.com/pages/contact', '/contact/'],
    ['https://orizen.com.au/', 'https://orizen.com.au/'],
    ['mailto:share@solagratia.com.au', 'mailto:share@solagratia.com.au'],
  ])('resolves %s to %s', (link, expected) => {
    expect(map.resolve(link)).toBe(expected);
  });

  it.each([
    [''],
    ['/search'],
    ['/cart'],
    ['/pages/data-sharing-opt-out'],
    ['shopify://collections/unknown'],
    ['https://shopify.com/107566563409/account/orders'],
    ['javascript:alert(1)'],
  ])('has no site URL for %s', (link) => {
    expect(map.resolve(link)).toBeUndefined();
  });

  it('lists every site URL it knows', () => {
    expect(map.allUrls().sort()).toEqual(
      [
        '/',
        '/contact/',
        '/our-collection/',
        '/our-story/',
        '/collections/face-care/',
        '/collections/rose-water-toners/',
        '/collections/masks-treatments/',
        '/products/rose-face-cream-3g/',
        '/products/rose-water-100ml/',
      ].sort(),
    );
  });

  it('refuses two collections whose titles make the same slug', () => {
    expect(() =>
      createUrlMap({
        ...input,
        collections: [
          { handle: 'a', title: 'Face Care' },
          { handle: 'b', title: 'Face & Care' },
        ],
      }),
    ).toThrow(/face-care/);
  });
});
