/**
 * Builds the content layer entries from the export. `src/content.config.ts` calls these as file
 * loader parsers, so each collection is read from its export file and reshaped by tested code.
 * Every theme-driven page is a section model of its Shopify template (`theme.ts`).
 */
import { siteConfig } from '../site.config.ts';
import { toCollections, toPages, toProducts } from './catalog.ts';
import { createSiteContext, PRODUCT_TEMPLATE, rawExport } from './export-data.ts';
import { escapeHtml } from './html.ts';
import { collectImageFiles } from './used-images.ts';
import type { RawCollection, RawPage, RawProduct, RawThemeContent } from './shopify-types.ts';
import { siteChrome, toNavigation, toSectionModel, type NavItem, type Section, type SiteChrome, type ThemeContext } from './theme.ts';
import type { BindingScope } from './theme-data.ts';

export const siteContext = createSiteContext(rawExport, { imageAlt: siteConfig.imageAlt });
const siteCollections = toCollections(rawExport.collections, siteContext);
const themeContext: ThemeContext = {
  ...siteContext,
  siteCollections,
  allProductHandles: siteContext.products.map((p) => p.handle),
};

export const parseProducts = (raw: RawProduct[]) => toProducts(raw, siteContext);
export const parseCollections = (raw: RawCollection[]) => toCollections(raw, siteContext);
export const parsePages = (raw: RawPage[]) => toPages(raw, siteContext);

export interface ThemePage {
  id: string;
  sections: Section[];
  skipped: string[];
}

/** Pages with a fixed theme template; `page` names the Shopify page their `closest.page` is. */
const THEME_PAGES: ReadonlyArray<{ id: string; template: string; firstHeadingIsH1: boolean; page?: string }> = [
  { id: 'home', template: 'templates/index.json', firstHeadingIsH1: true },
  { id: 'our-story', template: 'templates/page.our-story.json', firstHeadingIsH1: false, page: 'our-story' },
  { id: 'our-collection', template: 'templates/page.our-collection.json', firstHeadingIsH1: true, page: 'our-collection' },
  { id: 'contact', template: 'templates/page.contact.json', firstHeadingIsH1: true, page: 'contact' },
  { id: 'not-found', template: 'templates/404.json', firstHeadingIsH1: true },
];

function themeSections(theme: RawThemeContent, id: string, template: string, firstHeadingIsH1: boolean, scope: BindingScope): ThemePage {
  const raw = theme.templates[template];
  if (!raw) throw new Error(`theme-content.json has no ${template}`);
  const model = toSectionModel(raw, themeContext, { firstHeadingIsH1, template, scope });
  for (const line of model.skipped) console.warn(`[theme] ${id}: skipped ${line}`);
  return { id, sections: model.sections, skipped: model.skipped };
}

export function parseThemePages(theme: RawThemeContent): ThemePage[] {
  return THEME_PAGES.map(({ id, template, firstHeadingIsH1, page }) => {
    const scope: BindingScope = {};
    if (page) {
      const raw = rawExport.pages.find((p) => p.handle === page);
      if (!raw) throw new Error(`export has no page ${page} for ${template}`);
      scope.page = raw;
    }
    return themeSections(theme, id, template, firstHeadingIsH1, scope);
  });
}

/** A collection page: `templates/collection[.suffix].json` with the collection as `closest.collection`. */
export function collectionThemePage(handle: string): ThemePage {
  const collection = rawExport.collections.find((c) => c.handle === handle);
  if (!collection) throw new Error(`export has no collection ${handle}`);
  const template = collection.templateSuffix ? `templates/collection.${collection.templateSuffix}.json` : 'templates/collection.json';
  return themeSections(rawExport.theme, `collection ${handle}`, template, true, { collection });
}

/** A product page: the product template (`product.product-catalogue.json`) with the product as `closest.product`. */
export function productThemePage(handle: string): ThemePage {
  const product = siteContext.products.find((p) => p.handle === handle);
  if (!product) throw new Error(`export has no active product ${handle}`);
  const template = product.templateSuffix ? `templates/product.${product.templateSuffix}.json` : 'templates/product.json';
  if (template !== PRODUCT_TEMPLATE) throw new Error(`product ${handle} uses ${template}; the site reads ${PRODUCT_TEMPLATE}`);
  return themeSections(rawExport.theme, `product ${handle}`, template, true, { product });
}

export interface SiteSettings extends SiteChrome {
  id: 'site';
  shopName: string;
  navigation: NavItem[];
}

export function loadSiteSettings(): SiteSettings[] {
  const chrome = siteChrome(rawExport.theme, siteContext);
  const announcement = siteConfig.announcement;
  const header =
    announcement === undefined
      ? chrome.header
      : chrome.header.map((section) => ({
          ...section,
          // An empty string hides the bar; a string replaces the theme's text.
          blocks: section.blocks.flatMap((b) =>
            b.type !== '_announcement' ? [b] : announcement ? [{ ...b, html: `<p>${escapeHtml(announcement)}</p>` }] : [],
          ),
        }));
  return [{ id: 'site', ...chrome, header, shopName: rawExport.shop.name, navigation: toNavigation(rawExport.menus, 'main-menu', siteContext.urls) }];
}

/** Every exported image a page uses. Only these are imported, so unused originals never reach dist/. */
export function usedImageFiles(): string[] {
  return collectImageFiles(
    parseProducts(rawExport.products),
    parseCollections(rawExport.collections),
    parseThemePages(rawExport.theme),
    rawExport.collections.filter((c) => c.handle !== 'frontpage').map((c) => collectionThemePage(c.handle)),
    loadSiteSettings(),
  );
}

/** Content layer entries for the images collection: paths relative to export/images/manifest.json. */
export function parseImages(): Array<{ id: string; src: string }> {
  return usedImageFiles().map((file) => ({ id: file, src: `./${file}` }));
}
