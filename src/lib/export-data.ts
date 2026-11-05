/**
 * The raw Shopify export, imported as JSON, and the shared context every content parser needs
 * (the URL map, the image resolver, the product detail sections chosen in the theme).
 */
import shop from '../../export/shopify/shop.json';
import products from '../../export/shopify/products.json';
import collections from '../../export/shopify/collections.json';
import pages from '../../export/shopify/pages.json';
import menus from '../../export/shopify/menus.json';
import files from '../../export/shopify/files.json';
import manifest from '../../export/images/manifest.json';
import theme from '../../export/theme-content.json';
import { createImageResolver, type ImageResolver } from './images.ts';
import type { RawCollection, RawExport, RawProduct } from './shopify-types.ts';
import { productDetailSections, type DetailSection } from './theme.ts';
import { createUrlMap, type UrlMap } from './urls.ts';

export const rawExport: RawExport = {
  shop: shop as RawExport['shop'],
  products: products as RawExport['products'],
  collections: collections as RawExport['collections'],
  pages: pages as RawExport['pages'],
  menus: menus as RawExport['menus'],
  files: files as RawExport['files'],
  manifest: manifest as unknown as RawExport['manifest'],
  theme: theme as unknown as RawExport['theme'],
};

export interface SiteContext {
  urls: UrlMap;
  images: ImageResolver;
  currencyCode: string;
  /** Product detail sections (metafield key and heading), in the order the theme shows them. */
  detailSections: DetailSection[];
  products: readonly RawProduct[];
  collections: readonly RawCollection[];
}

/** The product template the store's products use (`templateSuffix: product-catalogue`). */
export const PRODUCT_TEMPLATE = 'templates/product.product-catalogue.json';

export function activeProducts(raw: RawExport): RawProduct[] {
  return raw.products.filter((p) => p.status === 'ACTIVE');
}

export function createSiteContext(raw: RawExport, settings: { imageAlt: Readonly<Record<string, string>> }): SiteContext {
  const live = activeProducts(raw);
  const urls = createUrlMap({
    shopDomains: [raw.shop.myshopifyDomain, raw.shop.primaryDomain.host],
    pages: raw.pages.filter((p) => p.isPublished),
    collections: raw.collections,
    products: live,
  });
  const template = raw.theme.templates[PRODUCT_TEMPLATE];
  if (!template) throw new Error(`theme-content.json has no ${PRODUCT_TEMPLATE}`);
  return {
    urls,
    images: createImageResolver(raw.manifest, raw.files, settings.imageAlt),
    currencyCode: raw.shop.currencyCode,
    detailSections: productDetailSections(template),
    products: live,
    collections: raw.collections,
  };
}
