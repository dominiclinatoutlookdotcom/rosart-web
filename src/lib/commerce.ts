/** Small helpers for the product pages and site settings: enquiries, prices, structured data, site URL. */

export const DEFAULT_SITE_URL = 'https://rosart.com.au';

/** A `mailto:` link to the enquiry address with the product name in the subject. */
export function enquiryMailto(email: string, productName: string): string {
  return `mailto:${email}?subject=${encodeURIComponent(`Enquiry: ${productName}`)}`;
}

const ORIZEN_HOSTS = new Set(['orizen.com.au', 'www.orizen.com.au']);

/**
 * Where a product's "Shop in Orizen" button goes: the product's `custom.orizen_url` metafield when
 * it is an https URL on orizen.com.au (or www.), otherwise the ORIZEN search for the product title.
 * The link is used as given: no tracking parameters are added.
 */
export function orizenUrl(metafieldValue: string | null | undefined, productTitle: string): string {
  const candidate = metafieldValue?.trim();
  if (candidate) {
    try {
      const url = new URL(candidate);
      if (url.protocol === 'https:' && ORIZEN_HOSTS.has(url.hostname) && !url.port && !url.username && !url.password) {
        return candidate;
      }
    } catch {
      // Not a URL: fall back to the search.
    }
  }
  return `https://orizen.com.au/search?q=${encodeURIComponent(productTitle)}`;
}

export function formatPrice(price: { amount: string; currencyCode: string }): string {
  return new Intl.NumberFormat('en-AU', { style: 'currency', currency: price.currencyCode }).format(Number(price.amount));
}

/** The site origin from `SITE_URL` (or the default), without a trailing slash. */
export function resolveSiteUrl(value: string | undefined): string {
  const raw = value?.trim();
  if (!raw) return DEFAULT_SITE_URL;
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new Error(`SITE_URL "${raw}" is not a URL`);
  }
  if (url.protocol !== 'https:' && url.protocol !== 'http:') throw new Error(`SITE_URL "${raw}" must be http(s)`);
  if (url.pathname !== '/' || url.search || url.hash) throw new Error(`SITE_URL "${raw}" must be an origin, with no path`);
  return url.origin;
}

export interface ProductLdInput {
  name: string;
  description: string;
  url: string;
  images: string[];
  sku: string | null;
  category: string;
  brand: string;
}

/** schema.org Product data. No `offers`: the site shows products but does not sell them. */
export function productJsonLd(input: ProductLdInput): Record<string, unknown> {
  return {
    '@context': 'https://schema.org',
    '@type': 'Product',
    name: input.name,
    description: input.description,
    url: input.url,
    image: input.images,
    ...(input.sku ? { sku: input.sku } : {}),
    ...(input.category ? { category: input.category } : {}),
    brand: { '@type': 'Brand', name: input.brand },
  };
}

/** Serialises JSON-LD for a `<script>` element, escaping `<` so content cannot close the tag. */
export function jsonLdScript(data: unknown): string {
  return JSON.stringify(data).replace(/</g, '\\u003c');
}
