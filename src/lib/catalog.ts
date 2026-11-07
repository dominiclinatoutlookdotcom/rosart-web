/**
 * Reshapes the exported products, collections and pages into what the site renders. These run
 * as content layer parsers (`src/content.config.ts`); the export files themselves are never edited.
 */
import { orizenUrl } from './commerce.ts';
import type { SiteContext } from './export-data.ts';
import { htmlToText, sanitizeHtml } from './html.ts';
import type { ImageRef } from './images.ts';
import { metafieldToHtml } from './richtext.ts';
import type { RawCollection, RawPage, RawProduct } from './shopify-types.ts';
import { truncate } from './text.ts';
import { HOMEPAGE_COLLECTION_HANDLE, SKIPPED_PAGE_HANDLES } from './urls.ts';

export interface Seo {
  title: string;
  description: string;
}

export interface ProductDetail {
  key: string;
  heading: string;
  html: string;
}

export interface Product {
  id: string;
  handle: string;
  title: string;
  url: string;
  descriptionHtml: string;
  productType: string;
  vendor: string;
  sku: string | null;
  price: { amount: string; currencyCode: string };
  seo: Seo;
  images: ImageRef[];
  details: ProductDetail[];
  /** The collection shown in the breadcrumb (never the homepage list). */
  collection: { title: string; url: string } | null;
  /** The ORIZEN link: metafield `custom.orizen_product_url` (the one the theme's button reads) if valid, else the ORIZEN search. */
  orizenUrl: string;
}

export interface Collection {
  id: string;
  slug: string;
  handle: string;
  title: string;
  url: string;
  descriptionHtml: string;
  image: ImageRef | null;
  productHandles: string[];
  seo: Seo;
}

export interface Page {
  id: string;
  handle: string;
  title: string;
  url: string;
  bodyHtml: string;
  /** Empty when Shopify holds no SEO description; the page then derives one from its content. */
  seo: { title: string; description: string };
}

function required<T>(value: T | undefined, what: string): T {
  if (value === undefined) throw new Error(`No site URL for ${what}`);
  return value;
}

function metafield(product: RawProduct, namespace: string, key: string) {
  return product.metafields.find((m) => m.namespace === namespace && m.key === key);
}

function primaryCollection(handle: string, ctx: SiteContext): Product['collection'] {
  for (const collection of ctx.collections) {
    if (collection.handle === HOMEPAGE_COLLECTION_HANDLE || !collection.productHandles.includes(handle)) continue;
    const url = ctx.urls.collection(collection.handle);
    if (url) return { title: collection.title, url };
  }
  return null;
}

export function toProducts(raw: readonly RawProduct[], ctx: SiteContext): Product[] {
  const resolveLink = ctx.urls.resolve;
  return raw
    .filter((p) => p.status === 'ACTIVE')
    .map((p): Product => {
      const variant = p.variants[0];
      if (!variant) throw new Error(`Product ${p.handle} has no variant`);
      const descriptionHtml = sanitizeHtml(p.descriptionHtml, { resolveLink });
      const details = ctx.detailSections.flatMap(({ key, heading }) => {
        const field = metafield(p, 'custom', key);
        const html = field ? metafieldToHtml(field, { resolveLink }) : '';
        return html ? [{ key, heading, html }] : [];
      });
      return {
        id: p.handle,
        handle: p.handle,
        title: p.title,
        url: required(ctx.urls.product(p.handle), `product ${p.handle}`),
        descriptionHtml,
        productType: p.productType,
        vendor: p.vendor,
        sku: variant.sku,
        price: { amount: variant.price, currencyCode: ctx.currencyCode },
        seo: {
          title: p.seo.title || metafield(p, 'global', 'title_tag')?.value || p.title,
          description:
            p.seo.description || metafield(p, 'global', 'description_tag')?.value || truncate(htmlToText(descriptionHtml)),
        },
        images: p.images.map((image) => ctx.images.fromUrl(image.url, image.altText, p.title)),
        details,
        collection: primaryCollection(p.handle, ctx),
        orizenUrl: orizenUrl(metafield(p, 'custom', 'orizen_product_url')?.value, p.title),
      };
    });
}

export function toCollections(raw: readonly RawCollection[], ctx: SiteContext): Collection[] {
  const titles = new Map(ctx.products.map((p) => [p.handle, p.title]));
  return raw
    .filter((c) => c.handle !== HOMEPAGE_COLLECTION_HANDLE)
    .map((c): Collection => {
      const slug = required(ctx.urls.collectionSlug(c.handle), `collection ${c.handle}`);
      const productHandles = c.productHandles.filter((h) => titles.has(h));
      const descriptionHtml = sanitizeHtml(c.descriptionHtml, { resolveLink: ctx.urls.resolve });
      const derived = `${c.title} by Rosart: ${productHandles.map((h) => titles.get(h)).join(', ')}.`;
      return {
        id: slug,
        slug,
        handle: c.handle,
        title: c.title,
        url: `/collections/${slug}/`,
        descriptionHtml,
        image: c.image ? ctx.images.fromUrl(c.image.url, c.image.altText) : null,
        productHandles,
        seo: {
          title: c.seo.title || c.title,
          description: c.seo.description || truncate(htmlToText(descriptionHtml) || derived),
        },
      };
    });
}

export function toPages(raw: readonly RawPage[], ctx: SiteContext): Page[] {
  return raw
    .filter((p) => p.isPublished && !SKIPPED_PAGE_HANDLES.includes(p.handle))
    .map((p): Page => {
      const bodyHtml = sanitizeHtml(p.bodyHtml, { resolveLink: ctx.urls.resolve });
      return {
        id: p.handle,
        handle: p.handle,
        title: p.title,
        url: required(ctx.urls.page(p.handle), `page ${p.handle}`),
        bodyHtml,
        seo: {
          title: p.seo.title || p.title,
          description: p.seo.description || truncate(htmlToText(bodyHtml)),
        },
      };
    });
}
