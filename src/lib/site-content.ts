/**
 * Builds the content layer entries from the export. `src/content.config.ts` calls these as file
 * loader parsers, so each collection is read from its export file and reshaped by tested code.
 */
import { siteConfig } from '../site.config.ts';
import { featuredProductHandles, toCollections, toPages, toProducts } from './catalog.ts';
import { createSiteContext, PRODUCT_TEMPLATE, rawExport } from './export-data.ts';
import type { ImageRef } from './images.ts';
import { collectImageFiles } from './used-images.ts';
import type { RawCollection, RawPage, RawProduct, RawThemeContent } from './shopify-types.ts';
import {
  firstBackground,
  productPurchaseNote,
  recommendationsHeading,
  siteChrome,
  toNavigation,
  toSectionModel,
  withFeaturedProducts,
  type NavItem,
  type Section,
  type SiteChrome,
} from './theme.ts';

export const siteContext = createSiteContext(rawExport, { imageAlt: siteConfig.imageAlt });
const siteCollections = toCollections(rawExport.collections, siteContext);

export const parseProducts = (raw: RawProduct[]) => toProducts(raw, siteContext);
export const parseCollections = (raw: RawCollection[]) => toCollections(raw, siteContext);
export const parsePages = (raw: RawPage[]) => toPages(raw, siteContext);

export interface ThemePage {
  id: string;
  sections: Section[];
  skipped: string[];
}

const THEME_PAGES: ReadonlyArray<{ id: string; template: string; firstHeadingIsH1: boolean; featured?: boolean }> = [
  { id: 'home', template: 'templates/index.json', firstHeadingIsH1: true, featured: true },
  { id: 'our-story', template: 'templates/page.our-story.json', firstHeadingIsH1: false },
  { id: 'our-collection', template: 'templates/page.our-collection.json', firstHeadingIsH1: false },
  { id: 'not-found', template: 'templates/404.json', firstHeadingIsH1: true },
];

export function parseThemePages(theme: RawThemeContent): ThemePage[] {
  const ctx = { ...siteContext, siteCollections };
  return THEME_PAGES.map(({ id, template, firstHeadingIsH1, featured }) => {
    const raw = theme.templates[template];
    if (!raw) throw new Error(`theme-content.json has no ${template}`);
    const model = toSectionModel(raw, ctx, { firstHeadingIsH1 });
    for (const line of model.skipped) console.warn(`[theme] ${id}: skipped ${line}`);
    return { id, sections: featured ? withFeaturedProducts(model.sections) : model.sections, skipped: model.skipped };
  });
}

export interface SiteSettings extends SiteChrome {
  id: 'site';
  navigation: NavItem[];
  featuredProductHandles: string[];
  collectionBand: ImageRef | null;
  recommendationsHeading: string | null;
  /** The theme's note under the product buy button ("You'll be redirected to ORIZEN..."). */
  purchaseNote: string | null;
}

export function loadSiteSettings(): SiteSettings[] {
  const theme = rawExport.theme;
  const chrome = siteChrome(theme, siteContext);
  const productTemplate = theme.templates[PRODUCT_TEMPLATE];
  const collectionTemplate = theme.templates['templates/collection.json'];
  return [
    {
      id: 'site',
      ...chrome,
      announcement: siteConfig.announcement ?? chrome.announcement,
      navigation: toNavigation(rawExport.menus, 'main-menu', siteContext.urls),
      featuredProductHandles: featuredProductHandles(rawExport.collections),
      collectionBand: collectionTemplate ? firstBackground(collectionTemplate, siteContext.images) : null,
      recommendationsHeading: productTemplate ? recommendationsHeading(productTemplate) : null,
      purchaseNote: productTemplate ? productPurchaseNote(productTemplate) : null,
    },
  ];
}

/** Every exported image a page uses. Only these are imported, so unused originals never reach dist/. */
export function usedImageFiles(): string[] {
  return collectImageFiles(
    parseProducts(rawExport.products),
    parseCollections(rawExport.collections),
    parseThemePages(rawExport.theme),
    loadSiteSettings(),
  );
}

/** Content layer entries for the images collection: paths relative to export/images/manifest.json. */
export function parseImages(): Array<{ id: string; src: string }> {
  return usedImageFiles().map((file) => ({ id: file, src: `./${file}` }));
}
