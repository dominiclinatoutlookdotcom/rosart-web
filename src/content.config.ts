import { defineCollection } from 'astro:content';
import { file } from 'astro/loaders';
import { z } from 'astro/zod';
import type { Collection, Page, Product } from './lib/catalog.ts';
import {
  loadSiteSettings,
  parseCollections,
  parseImages,
  parsePages,
  parseProducts,
  parseThemePages,
  type SiteSettings,
  type ThemePage,
} from './lib/site-content.ts';

/** The file loader types entries as plain records; ours are typed interfaces with an `id`. */
const entries = <T extends { id: string }>(items: T[]): Array<Record<string, unknown>> =>
  items as unknown as Array<Record<string, unknown>>;

// Every collection is read from the export and reshaped by tested modules in src/lib/. The
// shapes are checked by those modules' tests, so the schemas here only carry the types.
const products = defineCollection({
  loader: file('export/shopify/products.json', { parser: (text) => entries(parseProducts(JSON.parse(text))) }),
  schema: z.custom<Product>(),
});

const shopCollections = defineCollection({
  loader: file('export/shopify/collections.json', { parser: (text) => entries(parseCollections(JSON.parse(text))) }),
  schema: z.custom<Collection>(),
});

const pages = defineCollection({
  loader: file('export/shopify/pages.json', { parser: (text) => entries(parsePages(JSON.parse(text))) }),
  schema: z.custom<Page>(),
});

const themePages = defineCollection({
  loader: file('export/theme-content.json', { parser: (text) => entries(parseThemePages(JSON.parse(text))) }),
  schema: z.custom<ThemePage>(),
});

// Only the images some page uses; `image()` hands them to Astro's image pipeline. Paths are
// relative to the manifest, which sits beside the images in export/images/.
const images = defineCollection({
  loader: file('export/images/manifest.json', { parser: () => entries(parseImages()) }),
  schema: ({ image }) => z.object({ src: image() }),
});

const site = defineCollection({
  loader: () => loadSiteSettings(),
  schema: z.custom<SiteSettings>(),
});

export const collections = { products, collections: shopCollections, pages, themePages, images, site };
