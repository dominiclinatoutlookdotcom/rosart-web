/**
 * The one place that turns Shopify handles and links into site URLs.
 *
 * Menus, collection tiles, theme buttons and product pages all resolve their links here, so a
 * page that moves only moves in this file. Site URLs always end in a slash (`trailingSlash:
 * 'always'`), because CloudFront maps `/x/` to `/x/index.html`.
 */

export interface UrlMapInput {
  /** Hosts of the old Shopify store: absolute links to them are treated as site-relative. */
  shopDomains: readonly string[];
  pages: ReadonlyArray<{ handle: string; title: string }>;
  collections: ReadonlyArray<{ handle: string; title: string }>;
  products: ReadonlyArray<{ handle: string }>;
}

export interface UrlMap {
  page(handle: string): string | undefined;
  collection(handle: string): string | undefined;
  product(handle: string): string | undefined;
  /** The clean slug of a collection (from its title), or undefined for unknown or homepage handles. */
  collectionSlug(handle: string): string | undefined;
  /**
   * Resolves a Shopify link (`shopify://…`, `/pages/…`, `/collections/…`, `/products/…`, an
   * absolute link to the old store) to a site URL. External http(s), mailto and tel links pass
   * through. Returns undefined for anything with no place on the static site (search, cart,
   * accounts, the privacy-choices page, unknown handles, other schemes).
   */
  resolve(link: string | null | undefined): string | undefined;
  /** Every page URL the map can produce. */
  allUrls(): string[];
}

/** Shopify pages with no place on the static site (it has no tracking, so no privacy choices). */
export const SKIPPED_PAGE_HANDLES: readonly string[] = ['data-sharing-opt-out'];
/** The Shopify collection that holds the homepage's featured products. */
export const HOMEPAGE_COLLECTION_HANDLE = 'frontpage';
/** Where Shopify's virtual "all products" collection goes. */
const ALL_PRODUCTS_PAGE_HANDLE = 'our-collection';

export function slugify(text: string): string {
  return text
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
    .replace(/&/g, ' ')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

export function createUrlMap(input: UrlMapInput): UrlMap {
  const pages = new Map<string, string>();
  for (const page of input.pages) {
    if (SKIPPED_PAGE_HANDLES.includes(page.handle)) continue;
    pages.set(page.handle, `/${page.handle}/`);
  }

  const collectionSlugs = new Map<string, string>();
  const slugOwners = new Map<string, string>();
  for (const collection of input.collections) {
    if (collection.handle === HOMEPAGE_COLLECTION_HANDLE) continue;
    const slug = slugify(collection.title);
    if (!slug) throw new Error(`Collection "${collection.handle}" has a title with no usable slug`);
    const owner = slugOwners.get(slug);
    if (owner !== undefined) {
      throw new Error(`Collections "${owner}" and "${collection.handle}" both map to /collections/${slug}/`);
    }
    slugOwners.set(slug, collection.handle);
    collectionSlugs.set(collection.handle, slug);
  }

  const products = new Map<string, string>();
  for (const product of input.products) products.set(product.handle, `/products/${product.handle}/`);

  const shopHosts = new Set(input.shopDomains.map((d) => d.toLowerCase()));

  const page = (handle: string): string | undefined => pages.get(handle);
  const collection = (handle: string): string | undefined => {
    if (handle === HOMEPAGE_COLLECTION_HANDLE) return '/';
    if (handle === 'all') return page(ALL_PRODUCTS_PAGE_HANDLE);
    const slug = collectionSlugs.get(handle);
    return slug === undefined ? undefined : `/collections/${slug}/`;
  };
  const product = (handle: string): string | undefined => products.get(handle);

  function resolvePath(path: string): string | undefined {
    const segments = path.split('/').filter(Boolean).map(decodeURIComponent);
    if (segments.length === 0) return '/';
    const [kind, handle, sub, subHandle] = segments;
    if (kind === 'pages' && handle && segments.length === 2) return page(handle);
    if (kind === 'products' && handle && segments.length === 2) return product(handle);
    if (kind === 'collections') {
      if (segments.length === 1) return page(ALL_PRODUCTS_PAGE_HANDLE);
      if (handle && segments.length === 2) return collection(handle);
      if (sub === 'products' && subHandle && segments.length === 4) return product(subHandle);
    }
    return undefined;
  }

  function resolve(link: string | null | undefined): string | undefined {
    const raw = (link ?? '').trim();
    if (!raw) return undefined;

    const shopify = /^shopify:\/\/(pages|collections|products)\/([^/?#]+)$/.exec(raw);
    if (shopify) {
      const [, kind, handle] = shopify as unknown as [string, string, string];
      return resolvePath(`/${kind}/${handle}`);
    }

    if (/^(mailto|tel):/i.test(raw)) return raw;

    let url: URL;
    try {
      url = new URL(raw, 'https://site.invalid');
    } catch {
      return undefined;
    }
    const isRelative = url.host === 'site.invalid';
    const host = url.host.toLowerCase();
    if (!isRelative && !shopHosts.has(host)) {
      // Shopify-hosted pages (customer accounts, checkout, other stores) die with the store.
      if (/(^|\.)(my)?shopify\.com$/.test(host)) return undefined;
      return url.protocol === 'https:' || url.protocol === 'http:' ? raw : undefined;
    }
    const resolved = resolvePath(url.pathname);
    return resolved === undefined ? undefined : `${resolved}${url.hash}`;
  }

  return {
    page,
    collection,
    product,
    collectionSlug: (handle) => collectionSlugs.get(handle),
    resolve,
    allUrls: () => ['/', ...pages.values(), ...[...collectionSlugs.values()].map((s) => `/collections/${s}/`), ...products.values()],
  };
}
