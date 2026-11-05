/**
 * Maps the export's image references (CDN URLs and theme `shopify://shop_images/<name>` picks) to
 * files in `export/images/`, using `manifest.json`. The site never links to the CDN: every image
 * is a local file that Astro optimises at build time.
 */
import type { RawFile, RawManifest } from './shopify-types.ts';

export interface ImageRef {
  /** File name inside `export/images/`. */
  file: string;
  /** Alt text; empty means decorative. */
  alt: string;
}

export interface ImageResolver {
  fromUrl(url: string, alt?: string | null, fallbackAlt?: string): ImageRef;
  fromShopImage(ref: string, alt?: string | null, fallbackAlt?: string): ImageRef;
  /** Either kind of reference. */
  resolve(ref: string, alt?: string | null, fallbackAlt?: string): ImageRef;
}

const SHOP_IMAGE_PREFIX = 'shopify://shop_images/';

function canonicalUrl(url: string): string {
  const parsed = new URL(url);
  return `${parsed.origin}${parsed.pathname}`;
}

/**
 * @param imageAlt alt text per exported file name, for images Shopify holds no alt text for
 *   (site config `imageAlt`).
 */
export function createImageResolver(
  manifest: RawManifest,
  files: readonly RawFile[],
  imageAlt: Readonly<Record<string, string>>,
): ImageResolver {
  const fileAlt = new Map<string, string>();
  for (const file of files) {
    const local = manifest.sources[canonicalUrl(file.url)];
    if (local && file.alt) fileAlt.set(local, file.alt);
  }

  const pickAlt = (file: string, alt: string | null | undefined, fallbackAlt: string | undefined): string =>
    alt?.trim() || imageAlt[file] || fileAlt.get(file) || fallbackAlt || '';

  const fromUrl = (url: string, alt?: string | null, fallbackAlt?: string): ImageRef => {
    const file = manifest.sources[canonicalUrl(url)];
    if (!file) throw new Error(`Image ${url} is not in export/images/manifest.json`);
    return { file, alt: pickAlt(file, alt, fallbackAlt) };
  };

  const fromShopImage = (ref: string, alt?: string | null, fallbackAlt?: string): ImageRef => {
    const name = ref.startsWith(SHOP_IMAGE_PREFIX) ? ref.slice(SHOP_IMAGE_PREFIX.length) : ref;
    const file = manifest.shop_images[name];
    if (!file) throw new Error(`Theme image ${ref} is not in export/images/manifest.json`);
    return { file, alt: pickAlt(file, alt, fallbackAlt) };
  };

  return {
    fromUrl,
    fromShopImage,
    resolve: (ref, alt, fallbackAlt) =>
      ref.startsWith(SHOP_IMAGE_PREFIX) ? fromShopImage(ref, alt, fallbackAlt) : fromUrl(ref, alt, fallbackAlt),
  };
}
