/**
 * The frozen export snapshot: a copy of the Shopify export (JSON only, no image files) taken on
 * 2026-11-12. Tests that pin exact content (headings, product counts, logo lists) run against it
 * so a staff edit in Shopify never fails a publish. Never refresh it from the box's live export
 * as part of a publish; refresh it on purpose, with the tests that pin it, when the code changes.
 */
import shop from './shopify/shop.json';
import products from './shopify/products.json';
import collections from './shopify/collections.json';
import pages from './shopify/pages.json';
import menus from './shopify/menus.json';
import files from './shopify/files.json';
import manifest from './images-manifest.json';
import theme from './theme-content.json';
import type { RawExport } from '../../lib/shopify-types.ts';

export const fixtureExport: RawExport = {
  shop: shop as RawExport['shop'],
  products: products as RawExport['products'],
  collections: collections as RawExport['collections'],
  pages: pages as RawExport['pages'],
  menus: menus as RawExport['menus'],
  files: files as RawExport['files'],
  manifest: manifest as unknown as RawExport['manifest'],
  theme: theme as unknown as RawExport['theme'],
};
