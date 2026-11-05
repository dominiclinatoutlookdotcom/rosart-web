import { describe, expect, it } from 'vitest';
import { enquiryMailto, formatPrice, productJsonLd, resolveSiteUrl } from './commerce.ts';

describe('enquiryMailto', () => {
  it('addresses the enquiry email with the product name in the subject', () => {
    expect(enquiryMailto('info@rosart.com.au', 'Rose Face Soufflé Cream 3g')).toBe(
      'mailto:info@rosart.com.au?subject=Enquiry%3A%20Rose%20Face%20Souffl%C3%A9%20Cream%203g',
    );
  });

  it('encodes characters that would break the mailto URL', () => {
    expect(enquiryMailto('info@rosart.com.au', 'A & B?')).toBe('mailto:info@rosart.com.au?subject=Enquiry%3A%20A%20%26%20B%3F');
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
