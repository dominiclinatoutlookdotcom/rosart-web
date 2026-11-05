/**
 * Reads the owner-written copy of the Shopify theme (`export/theme-content.json`) into a section
 * model the site renders. Disabled sections and blocks are skipped. Nothing here comes from the
 * theme's code: only the text, image picks and links the owner chose.
 */
import type { Collection } from './catalog.ts';
import { parseConnections, type Connection } from './connections.ts';
import { htmlToText, sanitizeHtml } from './html.ts';
import type { ImageRef, ImageResolver } from './images.ts';
import type { RawMenu, RawMenuItem, RawThemeBlock, RawThemeContent, RawThemeSection } from './shopify-types.ts';
import { truncate } from './text.ts';
import { slugify, type UrlMap } from './urls.ts';

export interface DetailSection {
  /** Metafield key in the `custom` namespace. */
  key: string;
  heading: string;
}

export type Block =
  | { kind: 'heading'; text: string; level: number }
  | { kind: 'eyebrow'; text: string }
  | { kind: 'richtext'; html: string }
  | { kind: 'image'; image: ImageRef }
  | { kind: 'button'; label: string; href: string }
  | { kind: 'group'; blocks: Block[]; href?: string };

export type Section =
  | { kind: 'hero'; id: string; image: ImageRef | null; mobileImage: ImageRef | null; blocks: Block[] }
  | { kind: 'content'; id: string; background: ImageRef | null; blocks: Block[] }
  | { kind: 'page-title'; id: string; showTitle: boolean; showBody: boolean }
  | { kind: 'collection-list'; id: string; blocks: Block[]; collections: Collection[] }
  | { kind: 'product-list'; id: string; blocks: Block[] }
  | { kind: 'connections'; id: string; heading: string; intro: string; items: Connection[] }
  | { kind: 'featured-products'; id: string };

export interface ThemeContext {
  urls: UrlMap;
  images: ImageResolver;
  /** The site's collections, for collection lists and for linking collection tiles. */
  siteCollections: readonly Collection[];
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

/**
 * The product detail sections from the product template's accordion: each row heading names its
 * metafield ("HOW TO USE" is `custom.how_to_use`).
 */
export function productDetailSections(template: { sections: readonly RawThemeSection[] }): DetailSection[] {
  const sections: DetailSection[] = [];
  for (const section of template.sections.filter((s) => !s.disabled)) {
    walkBlocks(section.blocks, (block) => {
      if (block.type !== '_accordion-row') return;
      const heading = stringSetting(block.settings, 'heading').trim();
      if (heading) sections.push({ key: slugify(heading).replace(/-/g, '_'), heading });
    });
  }
  return sections;
}

const SINGLE_HEADING = /^<h[1-6]>(?:<strong>)?([^<]*)(?:<\/strong>)?<\/h[1-6]>$/;
const BOLD_PARAGRAPH = /^<p><strong>([^<]*)<\/strong><\/p>$/;
const PLAIN_PARAGRAPH = /^<p>([^<]*)<\/p>$/;

class ModelBuilder {
  readonly skipped: string[] = [];
  private headingCount = 0;

  constructor(
    private readonly ctx: ThemeContext,
    private readonly firstHeadingIsH1: boolean,
  ) {}

  private nextLevel(): number {
    this.headingCount += 1;
    return this.headingCount === 1 && this.firstHeadingIsH1 ? 1 : 2;
  }

  /** Classifies a theme text setting: a lone heading or bold line is a heading, an all-caps line an eyebrow. */
  text(raw: string): Block | null {
    const html = sanitizeHtml(raw, { resolveLink: this.ctx.urls.resolve });
    if (!html) return null;
    const heading = SINGLE_HEADING.exec(html) ?? BOLD_PARAGRAPH.exec(html);
    if (heading) return { kind: 'heading', text: htmlToText(html), level: this.nextLevel() };
    const plain = PLAIN_PARAGRAPH.exec(html);
    if (plain) {
      const text = htmlToText(html);
      if (/[A-Z]/.test(text) && text === text.toUpperCase()) return { kind: 'eyebrow', text };
    }
    return { kind: 'richtext', html };
  }

  blocks(raw: readonly RawThemeBlock[], sectionId: string): Block[] {
    const out: Block[] = [];
    for (const block of enabledBlocks(raw)) {
      const converted = this.block(block, sectionId);
      if (converted) out.push(converted);
    }
    return out;
  }

  /** Blocks of a list section: groups are flattened and the static card templates dropped. */
  listHeader(raw: readonly RawThemeBlock[], sectionId: string): Block[] {
    const out: Block[] = [];
    for (const block of enabledBlocks(raw)) {
      if (block.type === '_collection-card' || block.type === '_product-card') continue;
      if (block.type === 'group' || block.type === '_product-list-content') out.push(...this.listHeader(block.blocks, sectionId));
      else {
        const converted = this.block(block, sectionId);
        if (converted) out.push(converted);
      }
    }
    return out;
  }

  private block(block: RawThemeBlock, sectionId: string): Block | null {
    switch (block.type) {
      case 'text':
        return this.text(stringSetting(block.settings, 'text'));
      case 'image': {
        const ref = stringSetting(block.settings, 'image');
        return ref ? { kind: 'image', image: this.ctx.images.fromShopImage(ref) } : null;
      }
      case 'button': {
        const label = stringSetting(block.settings, 'label').trim();
        const link = stringSetting(block.settings, 'link');
        const href = this.ctx.urls.resolve(link);
        if (!label) return null;
        if (!href) {
          this.skipped.push(`button "${label}" in section ${sectionId}: link ${link || '(none)'} has no site page`);
          return null;
        }
        return { kind: 'button', label, href };
      }
      case 'group': {
        const blocks = this.blocks(block.blocks, sectionId);
        if (blocks.length === 0) return null;
        const href = this.collectionTileLink(blocks);
        return href ? { kind: 'group', blocks, href } : { kind: 'group', blocks };
      }
      default:
        this.skipped.push(`block ${block.id} (${block.type}) in section ${sectionId}: no site equivalent`);
        return null;
    }
  }

  /** A group with an image and a heading naming a collection becomes a link to that collection. */
  private collectionTileLink(blocks: readonly Block[]): string | undefined {
    if (!blocks.some((b) => b.kind === 'image')) return undefined;
    const heading = blocks.find((b) => b.kind === 'heading');
    if (!heading) return undefined;
    const title = heading.text.toLowerCase();
    return this.ctx.siteCollections.find((c) => c.title.toLowerCase() === title)?.url;
  }

  section(raw: RawThemeSection): Section | null {
    const { id, type, settings } = raw;
    switch (type) {
      case 'hero': {
        const image = stringSetting(settings, 'image_1');
        const mobile = stringSetting(settings, 'image_1_mobile');
        return {
          kind: 'hero',
          id,
          image: image ? this.ctx.images.fromShopImage(image) : null,
          mobileImage: mobile ? this.ctx.images.fromShopImage(mobile) : null,
          blocks: this.blocks(raw.blocks, id),
        };
      }
      case 'section':
      case 'main-404': {
        const bg = stringSetting(settings, 'background_image');
        const blocks = this.blocks(raw.blocks, id);
        if (blocks.length === 0) return null;
        return { kind: 'content', id, background: bg ? this.ctx.images.fromShopImage(bg) : null, blocks };
      }
      case 'main-page': {
        const enabled = enabledBlocks(raw.blocks);
        return {
          kind: 'page-title',
          id,
          showTitle: enabled.some((b) => b.id === 'heading' || b.id === 'title'),
          showBody: enabled.some((b) => b.type === 'page-content' || b.id === 'content'),
        };
      }
      case 'collection-list': {
        const handles = Array.isArray(settings['collection_list']) ? (settings['collection_list'] as unknown[]) : [];
        const collections = handles.flatMap((h) => this.ctx.siteCollections.filter((c) => c.handle === h));
        return { kind: 'collection-list', id, blocks: this.listHeader(raw.blocks, id), collections };
      }
      case 'product-list':
        return { kind: 'product-list', id, blocks: this.listHeader(raw.blocks, id) };
      case 'custom-liquid': {
        const parsed = parseConnections(stringSetting(settings, 'custom_liquid'), this.ctx.images);
        if (!parsed) {
          this.skipped.push(`section ${id} (custom-liquid): not a recognised layout`);
          return null;
        }
        return { kind: 'connections', id, ...parsed };
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
  options: { firstHeadingIsH1: boolean },
): SectionModel {
  const builder = new ModelBuilder(ctx, options.firstHeadingIsH1);
  const sections: Section[] = [];
  for (const raw of template.sections) {
    if (raw.disabled) continue;
    const section = builder.section(raw);
    if (section) sections.push(section);
  }
  return { sections, skipped: builder.skipped };
}

function isTileGrid(section: Section): boolean {
  if (section.kind !== 'content') return false;
  const groups = section.blocks.filter((b) => b.kind === 'group' && b.blocks.some((c) => c.kind === 'image'));
  return groups.length >= 2 && groups.length === section.blocks.length;
}

/** Inserts the homepage featured products after the collection tiles (or at the end). */
export function withFeaturedProducts(sections: readonly Section[]): Section[] {
  const featured: Section = { kind: 'featured-products', id: 'featured-products' };
  let index = -1;
  sections.forEach((s, i) => {
    if (isTileGrid(s)) index = i;
  });
  const at = index === -1 ? sections.length : index + 1;
  return [...sections.slice(0, at), featured, ...sections.slice(at)];
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
    const found = 'blocks' in section ? find(section.blocks) : undefined;
    if (found) return truncate(found);
  }
  return '';
}

/** The background image of the template's first enabled section that has one (the collection band). */
export function firstBackground(template: { sections: readonly RawThemeSection[] }, images: ImageResolver): ImageRef | null {
  for (const section of template.sections.filter((s) => !s.disabled)) {
    const ref = stringSetting(section.settings, 'background_image');
    if (ref) return images.fromShopImage(ref);
  }
  return null;
}

/** The heading the owner gave the product recommendations section, if it is enabled. */
export function recommendationsHeading(template: { sections: readonly RawThemeSection[] }): string | null {
  for (const section of template.sections.filter((s) => !s.disabled && s.type === 'product-recommendations')) {
    for (const block of enabledBlocks(section.blocks)) {
      const text = block.type === 'text' ? htmlToText(stringSetting(block.settings, 'text')) : '';
      if (text) return text;
    }
  }
  return null;
}

export interface SiteChrome {
  announcement: string;
  footerText: string;
  logo: ImageRef | null;
  showCopyright: boolean;
}

function groupSections(theme: RawThemeContent, name: string): RawThemeSection[] {
  return (theme.section_groups[`sections/${name}-group.json`]?.sections ?? []).filter((s) => !s.disabled);
}

function blockTexts(sections: readonly RawThemeSection[], sectionType: string, blockType: string): string[] {
  const texts: string[] = [];
  for (const section of sections.filter((s) => s.type === sectionType)) {
    walkBlocks(section.blocks, (block) => {
      if (block.type !== blockType) return;
      const text = htmlToText(stringSetting(block.settings, 'text'));
      if (text) texts.push(text);
    });
  }
  return texts;
}

/** Header and footer copy. Social links are left out: the theme holds only placeholder URLs. */
export function siteChrome(theme: RawThemeContent, ctx: { images: ImageResolver }): SiteChrome {
  const header = groupSections(theme, 'header');
  const footer = groupSections(theme, 'footer');
  let showCopyright = false;
  for (const section of footer) {
    walkBlocks(section.blocks, (block) => {
      if (block.type === 'footer-copyright') showCopyright = true;
    });
  }
  const logo = theme.global_settings['logo'];
  return {
    announcement: blockTexts(header, 'header-announcements', '_announcement')[0] ?? '',
    footerText: blockTexts(footer, 'footer', 'text').join(' '),
    logo: typeof logo === 'string' && logo ? ctx.images.fromShopImage(logo) : null,
    showCopyright,
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
