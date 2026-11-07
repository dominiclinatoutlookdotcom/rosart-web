/** Read helpers over the content layer collections defined in `src/content.config.ts`. */
import { getCollection, getEntry } from 'astro:content';
import type { Collection, Page, Product } from '../lib/catalog.ts';
import type { SiteSettings, ThemePage } from '../lib/site-content.ts';

export async function getSite(): Promise<SiteSettings> {
  const entry = await getEntry('site', 'site');
  if (!entry) throw new Error('Site settings are missing from the content layer');
  return entry.data;
}

export async function getProducts(): Promise<Product[]> {
  return (await getCollection('products')).map((e) => e.data);
}

export async function getProductsByHandle(handles: readonly string[]): Promise<Product[]> {
  const all = new Map((await getProducts()).map((p) => [p.handle, p]));
  return handles.flatMap((h) => {
    const product = all.get(h);
    return product ? [product] : [];
  });
}

export async function getShopCollections(): Promise<Collection[]> {
  return (await getCollection('collections')).map((e) => e.data);
}

export async function getPage(handle: string): Promise<Page> {
  const entry = await getEntry('pages', handle);
  if (!entry) throw new Error(`Page ${handle} is missing from the export`);
  return entry.data;
}

export async function getThemePage(id: string): Promise<ThemePage> {
  const entry = await getEntry('themePages', id);
  if (!entry) throw new Error(`Theme page ${id} is missing`);
  return entry.data;
}
