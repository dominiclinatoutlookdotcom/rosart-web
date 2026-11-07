/**
 * Reads the owner-written copy of the Shopify theme (`export/theme-content.json`) into a section
 * model the site renders: every enabled section and block in the theme's order, each carrying its
 * theme id and type (the components emit them, and the fidelity check reads them back). Liquid
 * bindings (`{{ closest.product.title }}`) resolve to the data they name. Nothing here comes from
 * the theme's code: only the text, image picks and links the owner chose. Intended differences
 * live in `allowed-differences.ts`.
 */
import { allowanceFor, allowanceWith } from './allowed-differences.ts';
import type { Collection } from './catalog.ts';
import { orizenUrl } from './commerce.ts';
import { parseConnections, type Connection } from './connections.ts';
import { htmlToText, sanitizeHtml } from './html.ts';
import type { ImageRef, ImageResolver } from './images.ts';
import type { RawMenu, RawMenuItem, RawThemeBlock, RawThemeContent, RawThemeSection } from './shopify-types.ts';
import { truncate } from './text.ts';
import { boundMetafieldKey, orderedBlocks, themeSetting, type BindingScope } from './theme-data.ts';
import type { UrlMap } from './urls.ts';

export interface DetailSection {
  /** Metafield key in the `custom` namespace. */
  key: string;
  heading: string;
}

/** The theme block or section a model node comes from: the site emits these as data attributes. */
export interface ThemeSource {
  id: string;
  /** The theme type (`text`, `group`, `_product-details` ...). */
  type: string;
}

export type Block = ThemeSource &
  (
    | { kind: 'heading'; text: string; level: number }
    | { kind: 'eyebrow'; text: string }
    | { kind: 'richtext'; html: string }
    | { kind: 'image'; image: ImageRef; href?: string }
    | { kind: 'button'; label: string; href: string; external?: boolean }
    /** A container: group, product details, list header, accordion. */
    | { kind: 'group'; blocks: Block[]; href?: string }
    | { kind: 'accordion-row'; heading: string; blocks: Block[] }
    | { kind: 'media-gallery'; images: ImageRef[] }
    | { kind: 'divider' }
    /** The allowed substitute for Shopify's contact form (see allowed-differences.ts). */
    | { kind: 'contact-form' }
  );

/** The theme's card template for a list: the blocks each card shows, in order. */
export interface CardTemplate extends ThemeSource {
  blocks: ThemeSource[];
}

export type Section = ThemeSource &
  (
    | { kind: 'hero'; image: ImageRef | null; mobileImage: ImageRef | null; blocks: Block[] }
    | { kind: 'content'; background: ImageRef | null; blocks: Block[] }
    /** A theme section with no enabled blocks: Shopify renders it as empty space, so does the site. */
    | { kind: 'spacer' }
    | { kind: 'collection-list'; before: Block[]; card: CardTemplate | null; collections: Collection[]; after: Block[] }
    /** A product list or collection grid: the products of the collection the theme names. */
    | { kind: 'product-list'; before: Block[]; card: CardTemplate | null; productHandles: string[]; after: Block[] }
    /** The product page's main section: media gallery and details. */
    | { kind: 'product'; blocks: Block[] }
    /** Shopify's product recommendations: its heading; the site lists related products in their place. */
    | { kind: 'recommendations'; blocks: Block[] }
    | { kind: 'connections'; heading: string; intro: string; items: Connection[] }
  );

export interface ThemeContext {
  urls: UrlMap;
  images: ImageResolver;
  /** The site's collections, for collection lists and product lists. */
  siteCollections: readonly Collection[];
  /** Handles of every active product, in export order (Shopify's `all` collection). */
  allProductHandles: readonly string[];
}

export interface ModelOptions {
  firstHeadingIsH1: boolean;
  /** The template path (`templates/index.json`), to find its allowed differences. */
  template: string;
  /** What `closest.*` points at on the page. */
  scope?: BindingScope;
}

export interface SectionModel {
  sections: Section[];
  /** Enabled content the site could not place, one line each, for the owner to review. */
  skipped: string[];
}

function enabledBlocks(blocks: readonly RawThemeBlock[]): RawThemeBlock[] {
  return blocks.filter((b) => !b.disabled);
}

function walkBlocks(blocks: readonly RawThemeBlock[], visit: (block: RawThemeBlock) => void): void {
  for (const block of enabledBlocks(blocks)) {
    visit(block);
    walkBlocks(block.blocks, visit);
  }
}

function stringSetting(settings: Record<string, unknown>, key: string): string {
  const value = settings[key];
  return typeof value === 'string' ? value : '';
}

function numberSetting(settings: Record<string, unknown>, key: string): number | undefined {
  const value = settings[key];
  return typeof value === 'number' ? value : undefined;
}

/**
 * The product detail sections from the product template's accordion: each row's heading, and the
 * metafield its text block is bound to (`{{ closest.product.metafields.custom.how_to_use }}`).
 */
export function productDetailSections(template: { sections: readonly RawThemeSection[] }): DetailSection[] {
  const sections: DetailSection[] = [];
  for (const section of template.sections.filter((s) => !s.disabled)) {
    walkBlocks(section.blocks, (block) => {
      if (block.type !== '_accordion-row') return;
      const heading = stringSetting(block.settings, 'heading').trim();
      let key: string | undefined;
      walkBlocks(block.blocks, (child) => {
        key ??= boundMetafieldKey(child.bindings?.['text'] ?? '');
      });
      if (heading && key) sections.push({ key, heading });
    });
  }
  return sections;
}

const SINGLE_HEADING = /^<h([1-6])>(?:<strong>)?([^<]*)(?:<\/strong>)?<\/h[1-6]>$/;
const BOLD_PARAGRAPH = /^<p><strong>([^<]*)<\/strong><\/p>$/;
const PLAIN_PARAGRAPH = /^<p>([^<]*)<\/p>$/;

class ModelBuilder {
  readonly skipped: string[] = [];
  private headingCount = 0;
  private readonly scope: BindingScope;

  constructor(
    private readonly ctx: ThemeContext,
    private readonly options: ModelOptions,
  ) {
    this.scope = options.scope ?? {};
  }

  /** The first heading is the h1 when the page wants one; the others keep the theme's level, at least h2. */
  private level(tagLevel: number | undefined): number {
    this.headingCount += 1;
    if (this.headingCount === 1 && this.options.firstHeadingIsH1) return 1;
    return Math.max(2, tagLevel ?? 2);
  }

  /** Classifies a theme text setting: a lone heading or bold line is a heading, an all-caps line an eyebrow. */
  private text(source: ThemeSource, raw: string): Block | null {
    const html = sanitizeHtml(raw, { resolveLink: this.ctx.urls.resolve });
    if (!html) return null;
    const tagged = SINGLE_HEADING.exec(html);
    if (tagged || BOLD_PARAGRAPH.exec(html)) {
      return { ...source, kind: 'heading', text: htmlToText(html), level: this.level(tagged ? Number(tagged[1]) : undefined) };
    }
    if (PLAIN_PARAGRAPH.exec(html)) {
      const text = htmlToText(html);
      if (/[A-Z]/.test(text) && text === text.toUpperCase()) return { ...source, kind: 'eyebrow', text };
    }
    return { ...source, kind: 'richtext', html };
  }

  blocks(parentType: string, raw: readonly RawThemeBlock[], sectionId: string): Block[] {
    const out: Block[] = [];
    for (const block of orderedBlocks(parentType, raw)) {
      const converted = this.block(block, sectionId);
      if (converted) out.push(converted);
    }
    return out;
  }

  private productImages(): ImageRef[] {
    const product = this.scope.product;
    if (!product) throw new Error(`template ${this.options.template} shows product media on a page with no product`);
    return product.images.map((image) => this.ctx.images.fromUrl(image.url, image.altText, product.title));
  }

  private block(block: RawThemeBlock, sectionId: string): Block | null {
    const source = { id: block.id, type: block.type };
    const allowance = allowanceFor(this.options.template, block.type, block.id);
    if (allowance?.effect === 'absent') return null;
    if (allowance?.effect === 'opaque' && block.type === 'contact-form') return { ...source, kind: 'contact-form' };
    const setting = (key: string) => themeSetting(block, key, this.scope);
    switch (block.type) {
      case 'text':
        return this.text(source, setting('text'));
      case 'page-content': {
        const page = this.scope.page;
        if (!page) throw new Error(`template ${this.options.template} shows page content on a page with no page`);
        const html = sanitizeHtml(page.bodyHtml, { resolveLink: this.ctx.urls.resolve });
        return html ? { ...source, kind: 'richtext', html } : null;
      }
      case 'image': {
        const ref = setting('image');
        if (!ref) return null;
        const href = this.ctx.urls.resolve(setting('link'));
        const image = this.ctx.images.fromShopImage(ref);
        return href ? { ...source, kind: 'image', image, href } : { ...source, kind: 'image', image };
      }
      case 'button': {
        const themeLabel = setting('label').trim();
        const link = setting('link');
        if (!themeLabel) return null;
        const label = allowanceWith(this.options.template, block.type, block.id, 'replaced-label')?.label ?? themeLabel;
        const href = this.ctx.urls.resolve(link);
        if (allowanceWith(this.options.template, block.type, block.id, 'fallback-link') && this.scope.product) {
          // Owner decision: every product links to ORIZEN (the product's URL, else a search).
          return { ...source, kind: 'button', label, href: orizenUrl(href, this.scope.product.title), external: true };
        }
        if (!href) {
          this.skipped.push(`button "${label}" in section ${sectionId}: link ${link || '(none)'} has no site page`);
          return null;
        }
        return /^https?:/.test(href)
          ? { ...source, kind: 'button', label, href, external: true }
          : { ...source, kind: 'button', label, href };
      }
      case 'group':
      case '_product-details':
      case '_product-list-content':
      case 'accordion': {
        const blocks = this.blocks(block.type, block.blocks, sectionId);
        const href = this.ctx.urls.resolve(setting('link'));
        return href ? { ...source, kind: 'group', blocks, href } : { ...source, kind: 'group', blocks };
      }
      case '_accordion-row':
        return {
          ...source,
          kind: 'accordion-row',
          heading: setting('heading').trim(),
          blocks: this.blocks(block.type, block.blocks, sectionId),
        };
      case '_product-media-gallery':
        return { ...source, kind: 'media-gallery', images: this.productImages() };
      case '_divider':
        return { ...source, kind: 'divider' };
      case 'disclosures':
        // Shopify shows disclosures only from the product's `shopify.disclosure` metafield.
        if (!this.scope.product?.metafields.some((m) => m.namespace === 'shopify' && m.key === 'disclosure')) return null;
        this.skipped.push(`block ${block.id} (disclosures) in section ${sectionId}: no site equivalent`);
        return null;
      default:
        this.skipped.push(`block ${block.id} (${block.type}) in section ${sectionId}: no site equivalent`);
        return null;
    }
  }

  /** The blocks of a list section around its card template, and the blocks each card shows. */
  private list(raw: RawThemeSection, cardType: string): { before: Block[]; card: CardTemplate | null; after: Block[] } {
    const before: Block[] = [];
    const after: Block[] = [];
    let card: CardTemplate | null = null;
    for (const block of orderedBlocks(raw.type, raw.blocks)) {
      if (block.type === cardType) {
        if (allowanceFor(this.options.template, block.type, block.id)?.effect === 'absent') continue;
        card = {
          id: block.id,
          type: block.type,
          blocks: orderedBlocks(block.type, block.blocks)
            .filter((b) => allowanceFor(this.options.template, b.type, b.id)?.effect !== 'absent')
            .map((b) => ({ id: b.id, type: b.type })),
        };
        continue;
      }
      const converted = this.block(block, raw.id);
      if (converted) (card ? after : before).push(converted);
    }
    return { before, card, after };
  }

  private productHandles(collection: string, limit: number | undefined): string[] {
    const active = this.ctx.allProductHandles;
    const handles =
      collection === 'all'
        ? [...active]
        : (this.ctx.siteCollections.find((c) => c.handle === collection)?.productHandles ?? []).filter((h) => active.includes(h));
    return limit === undefined ? handles : handles.slice(0, limit);
  }

  section(raw: RawThemeSection): Section | null {
    const { id, type, settings } = raw;
    const source = { id, type };
    switch (type) {
      case 'hero': {
        const image = stringSetting(settings, 'image_1');
        const mobile = stringSetting(settings, 'image_1_mobile');
        return {
          ...source,
          kind: 'hero',
          image: image ? this.ctx.images.fromShopImage(image) : null,
          mobileImage: mobile ? this.ctx.images.fromShopImage(mobile) : null,
          blocks: this.blocks(type, raw.blocks, id),
        };
      }
      case 'section':
      case 'main-404':
      case 'main-page': {
        const bg = stringSetting(settings, 'background_image');
        const blocks = this.blocks(type, raw.blocks, id);
        if (blocks.length === 0 && !bg) return { ...source, kind: 'spacer' };
        return { ...source, kind: 'content', background: bg ? this.ctx.images.fromShopImage(bg) : null, blocks };
      }
      case 'product-information':
        return { ...source, kind: 'product', blocks: this.blocks(type, raw.blocks, id) };
      case 'product-recommendations':
        return { ...source, kind: 'recommendations', blocks: this.list(raw, '_product-card').before };
      case 'collection-list': {
        const handles = Array.isArray(settings['collection_list']) ? (settings['collection_list'] as unknown[]) : [];
        const limit = numberSetting(settings, 'max_collections') ?? handles.length;
        const collections = handles.flatMap((h) => this.ctx.siteCollections.filter((c) => c.handle === h)).slice(0, limit);
        return { ...source, kind: 'collection-list', ...this.list(raw, '_collection-card'), collections };
      }
      case 'product-list':
        return {
          ...source,
          kind: 'product-list',
          ...this.list(raw, '_product-card'),
          productHandles: this.productHandles(stringSetting(settings, 'collection'), numberSetting(settings, 'max_products')),
        };
      case 'main-collection': {
        const collection = this.scope.collection;
        if (!collection) throw new Error(`template ${this.options.template} lists a collection on a page with no collection`);
        return {
          ...source,
          kind: 'product-list',
          ...this.list(raw, '_product-card'),
          productHandles: this.productHandles(collection.handle, numberSetting(settings, 'products_per_page')),
        };
      }
      case 'custom-liquid': {
        const parsed = parseConnections(stringSetting(settings, 'custom_liquid'), this.ctx.images);
        if (!parsed) {
          this.skipped.push(`section ${id} (custom-liquid): not a recognised layout`);
          return null;
        }
        return { ...source, kind: 'connections', ...parsed };
      }
      default:
        this.skipped.push(`section ${id} (${type}): no site equivalent`);
        return null;
    }
  }
}

export function toSectionModel(
  template: { sections: readonly RawThemeSection[] },
  ctx: ThemeContext,
  options: ModelOptions,
): SectionModel {
  const builder = new ModelBuilder(ctx, options);
  const sections: Section[] = [];
  for (const raw of template.sections) {
    if (raw.disabled) continue;
    const section = builder.section(raw);
    if (section) sections.push(section);
  }
  return { sections, skipped: builder.skipped };
}

function sectionBlocks(section: Section): readonly Block[] {
  switch (section.kind) {
    case 'hero':
    case 'content':
    case 'product':
    case 'recommendations':
      return section.blocks;
    case 'collection-list':
    case 'product-list':
      return [...section.before, ...section.after];
    default:
      return [];
  }
}

/** True when a section shows an h1 (the page then needs no hidden one). */
export function hasH1(sections: readonly Section[]): boolean {
  const search = (blocks: readonly Block[]): boolean =>
    blocks.some((b) => (b.kind === 'heading' && b.level === 1) || ((b.kind === 'group' || b.kind === 'accordion-row') && search(b.blocks)));
  return sections.some((s) => search(sectionBlocks(s)));
}

/** A meta description from the page's first real body text (one-line straplines are skipped). */
export function describeSections(sections: readonly Section[]): string {
  const find = (blocks: readonly Block[]): string | undefined => {
    for (const block of blocks) {
      if (block.kind === 'richtext') {
        const text = htmlToText(block.html);
        if (text.length >= 60) return text;
      } else if (block.kind === 'group') {
        const found = find(block.blocks);
        if (found) return found;
      }
    }
    return undefined;
  };
  for (const section of sections) {
    const found = find(sectionBlocks(section));
    if (found) return truncate(found);
  }
  return '';
}

/** One header or footer block: an announcement or footer text, or a structural block (logo, menu, copyright). */
export interface ChromeBlock extends ThemeSource {
  /** Sanitised HTML of a text block or announcement. */
  html?: string;
}

export interface ChromeSection extends ThemeSource {
  blocks: ChromeBlock[];
}

export interface SiteChrome {
  /** The header group's enabled sections (announcement bar, header), in theme order. */
  header: ChromeSection[];
  /** The footer group's enabled sections, in theme order, without the allowed absences. */
  footer: ChromeSection[];
  logo: ImageRef | null;
  /** The footer's text, for the homepage title. */
  footerText: string;
}

function chromeSections(theme: RawThemeContent, name: string, urls: UrlMap): ChromeSection[] {
  const group = `sections/${name}-group.json`;
  return (theme.section_groups[group]?.sections ?? [])
    .filter((s) => !s.disabled)
    .map((section) => ({
      id: section.id,
      type: section.type,
      blocks: orderedBlocks(section.type, section.blocks)
        .filter((b) => allowanceFor(group, b.type, b.id)?.effect !== 'absent')
        .map((block): ChromeBlock => {
          const text = stringSetting(block.settings, 'text');
          return text
            ? { id: block.id, type: block.type, html: sanitizeHtml(text, { resolveLink: urls.resolve }) }
            : { id: block.id, type: block.type };
        }),
    }));
}

/** The header and footer groups as the theme sets them, and the logo. */
export function siteChrome(theme: RawThemeContent, ctx: { images: ImageResolver; urls: UrlMap }): SiteChrome {
  const header = chromeSections(theme, 'header', ctx.urls);
  const footer = chromeSections(theme, 'footer', ctx.urls);
  const logo = theme.global_settings['logo'];
  const footerText = footer
    .filter((s) => s.type === 'footer')
    .flatMap((s) => s.blocks.filter((b) => b.type === 'text' && b.html).map((b) => htmlToText(b.html ?? '')))
    .join(' ');
  return {
    header,
    footer,
    logo: typeof logo === 'string' && logo ? ctx.images.fromShopImage(logo) : null,
    footerText,
  };
}

export interface NavItem {
  title: string;
  url: string;
  children: NavItem[];
}

function navItems(items: readonly RawMenuItem[], urls: UrlMap): NavItem[] {
  return items.flatMap((item) => {
    const url = urls.resolve(item.url);
    return url ? [{ title: item.title, url, children: navItems(item.items, urls) }] : [];
  });
}

/** A Shopify menu as site links; items with no site page (search, privacy choices) are dropped. */
export function toNavigation(menus: readonly RawMenu[], handle: string, urls: UrlMap): NavItem[] {
  const menu = menus.find((m) => m.handle === handle);
  return menu ? navItems(menu.items, urls) : [];
}
