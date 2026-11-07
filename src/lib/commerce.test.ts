import { describe, expect, it } from 'vitest';
import { formatPrice, orizenUrl, productJsonLd, resolveSiteUrl } from './commerce.ts';

describe('orizenUrl', () => {
  const title = 'Rose Face Soufflé Cream 3g';
  const search = 'https://orizen.com.au/search?q=Rose%20Face%20Souffl%C3%A9%20Cream%203g';

  it('uses the custom.orizen_product_url metafield when it is https on orizen.com.au', () => {
    expect(orizenUrl('https://orizen.com.au/products/rose-face-souffle-cream-3g', title)).toBe(
      'https://orizen.com.au/products/rose-face-souffle-cream-3g',
    );
    expect(orizenUrl('  https://www.orizen.com.au/products/x  ', title)).toBe('https://www.orizen.com.au/products/x');
  });

  it('falls back to the ORIZEN search for the product title when the metafield is missing or empty', () => {
    expect(orizenUrl(undefined, title)).toBe(search);
    expect(orizenUrl(null, title)).toBe(search);
    expect(orizenUrl('   ', title)).toBe(search);
  });

  it('falls back when the metafield points at another host', () => {
    expect(orizenUrl('https://example.com/products/x', title)).toBe(search);
    expect(orizenUrl('https://orizen.com.au.evil.example/x', title)).toBe(search);
    expect(orizenUrl('https://shop.orizen.com.au/x', title)).toBe(search);
    expect(orizenUrl('https://orizen.com.au@evil.example/x', title)).toBe(search);
  });

  it('falls back when the metafield is not https or not a URL', () => {
    expect(orizenUrl('http://orizen.com.au/products/x', title)).toBe(search);
    expect(orizenUrl('javascript:alert(1)//orizen.com.au', title)).toBe(search);
    expect(orizenUrl('orizen.com.au/products/x', title)).toBe(search);
    expect(orizenUrl('https://user:pw@orizen.com.au/x', title)).toBe(search);
  });

  it('encodes characters that would break the search URL', () => {
    expect(orizenUrl(undefined, 'A & B? #1')).toBe('https://orizen.com.au/search?q=A%20%26%20B%3F%20%231');
  });
});

describe('formatPrice', () => {
  it('formats AUD for Australia', () => {
    expect(formatPrice({ amount: '125.00', currencyCode: 'AUD' })).toBe('$125.00');
  });
});

describe('resolveSiteUrl', () => {
  it('defaults to the canonical domain', () => {
    expect(resolveSiteUrl(undefined)).toBe('https://rosart.com.au');
    expect(resolveSiteUrl('  ')).toBe('https://rosart.com.au');
  });

  it('accepts an origin and drops a trailing slash', () => {
    expect(resolveSiteUrl('https://staging.example.com/')).toBe('https://staging.example.com');
  });

  it('refuses a URL with a path or a non-http scheme', () => {
    expect(() => resolveSiteUrl('https://example.com/shop')).toThrow(/SITE_URL/);
    expect(() => resolveSiteUrl('ftp://example.com')).toThrow(/SITE_URL/);
    expect(() => resolveSiteUrl('not a url')).toThrow(/SITE_URL/);
  });
});

describe('productJsonLd', () => {
  const data = productJsonLd({
    name: 'Rose Water 100ml',
    description: 'Pure rose water.',
    url: 'https://rosart.com.au/products/rose-water-100ml/',
    images: ['https://rosart.com.au/_astro/a.webp'],
    sku: 'RW100',
    category: 'Facial Toner',
    brand: 'Rosart',
  });

  it('describes the product without offers (the site does not sell)', () => {
    expect(data).toEqual({
      '@context': 'https://schema.org',
      '@type': 'Product',
      name: 'Rose Water 100ml',
      description: 'Pure rose water.',
      url: 'https://rosart.com.au/products/rose-water-100ml/',
      image: ['https://rosart.com.au/_astro/a.webp'],
      sku: 'RW100',
      category: 'Facial Toner',
      brand: { '@type': 'Brand', name: 'Rosart' },
    });
    expect(data).not.toHaveProperty('offers');
  });
});
