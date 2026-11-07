/**
 * Theme fidelity: proves the site renders what the Shopify theme renders, nothing more and
 * nothing less. The theme side is an outline built from the exported template JSON (sections and
 * blocks in theme order, disabled ones dropped, Liquid bindings resolved from the export); the
 * site side is the same outline read back from the built HTML through the `data-theme-section`,
 * `data-theme-block` and `data-theme-type` attributes the components emit. Every place the static
 * site differs on purpose is listed once, with its reason, in `allowed-differences.ts`.
 *
 * Runs under plain Node too (the dist check): only `.ts` imports, no Vite features.
 */
import { parse, type DefaultTreeAdapterTypes } from 'parse5';
import { htmlToText } from './html.ts';
import { orizenUrl } from './commerce.ts';
import type { ImageResolver } from './images.ts';
import type { RawCollection, RawMenu, RawMenuItem, RawProduct, RawThemeBlock, RawThemeSection } from './shopify-types.ts';
import { ALLOWED_DIFFERENCES, allowanceFor, allowanceWith } from './allowed-differences.ts';
export { ALLOWED_DIFFERENCES } from './allowed-differences.ts';
import { orderedBlocks, themeSetting as setting, type BindingScope } from './theme-data.ts';
import type { UrlMap } from './urls.ts';

type Node = DefaultTreeAdapterTypes.ChildNode;
type Element = DefaultTreeAdapterTypes.Element;

/** One theme section or block: what it shows (its own text, images and links) and its children. */
export interface OutlineNode {
  id: string;
  type: string;
  /** Visible text the node itself shows (not its children's), whitespace collapsed. */
  text: string;
  /** Exported image files (names in `export/images/`). */
  images: string[];
  links: string[];
  children: OutlineNode[];
  /** An allowed substitute: only the id and type are compared. */
  opaque?: true;
}

export interface FidelityData {
  urls: UrlMap;
  images: ImageResolver;
  /** Active products, in export order. */
  products: readonly RawProduct[];
  collections: readonly RawCollection[];
  menus: readonly RawMenu[];
  shopName: string;
  /** The theme's logo setting (`shopify://shop_images/...`), if any. */
  logo: string | null;
  year: number;
}

/** The template being checked and what `closest.*` points at on the page. */
export interface Scope extends BindingScope {
  template: string;
}

function need<T>(value: T | undefined, what: string): T {
  if (value === undefined) throw new Error(`the theme needs ${what} here, which this page does not have`);
  return value;
}

const themeAllowance = (scope: Scope, type: string, id: string) => allowanceFor(scope.template, type, id);

function stringSetting(settings: Record<string, unknown>, key: string): string {
  const value = settings[key];
  return typeof value === 'string' ? value : '';
}

function numberSetting(settings: Record<string, unknown>, key: string): number | undefined {
  const value = settings[key];
  return typeof value === 'number' ? value : undefined;
}

const outline = (id: string, type: string, parts: Partial<OutlineNode> = {}): OutlineNode => ({
  id,
  type,
  text: '',
  images: [],
  links: [],
  children: [],
  ...parts,
});

function enabled<T extends { disabled: boolean }>(items: readonly T[]): T[] {
  return items.filter((item) => !item.disabled);
}

function menuOutline(items: readonly RawMenuItem[], data: FidelityData): { titles: string[]; links: string[] } {
  const titles: string[] = [];
  const links: string[] = [];
  for (const item of items) {
    const url = data.urls.resolve(item.url);
    if (!url) continue; // search, account and privacy-choices links have no static page
    titles.push(item.title);
    links.push(url);
    const nested = menuOutline(item.items, data);
    titles.push(...nested.titles);
    links.push(...nested.links);
  }
  return { titles, links };
}

class ExpectedBuilder {
  private readonly data: FidelityData;

  // No parameter property: the dist check runs this file with Node's type stripping.
  constructor(data: FidelityData) {
    this.data = data;
  }

  private productsOf(handle: string, limit: number | undefined): RawProduct[] {
    const all = handle === 'all' ? this.data.products : this.collectionProducts(handle);
    return limit === undefined ? [...all] : all.slice(0, limit);
  }

  private collectionProducts(handle: string): RawProduct[] {
    const collection = this.data.collections.find((c) => c.handle === handle);
    if (!collection) throw new Error(`theme names collection ${handle}, which is not in the export`);
    return collection.productHandles.flatMap((h) => this.data.products.filter((p) => p.handle === h));
  }

  private productCard(card: RawThemeBlock, product: RawProduct, scope: Scope): OutlineNode {
    const url = this.data.urls.product(product.handle);
    return outline(card.id, card.type, {
      links: url ? [url] : [],
      children: this.blocks(card.type, card.blocks, { ...scope, product }),
    });
  }

  blocks(parentType: string, blocks: readonly RawThemeBlock[], scope: Scope): OutlineNode[] {
    return orderedBlocks(parentType, blocks).flatMap((block) => this.block(block, scope));
  }

  /** The blocks of a list section, with the static card template repeated once per item. */
  private listBlocks(section: RawThemeSection, scope: Scope, cards: (card: RawThemeBlock) => OutlineNode[]): OutlineNode[] {
    return orderedBlocks(section.type, section.blocks).flatMap((block) =>
      block.type === '_product-card' || block.type === '_collection-card' ? cards(block) : this.block(block, scope),
    );
  }

  block(block: RawThemeBlock, scope: Scope): OutlineNode[] {
    const allowance = themeAllowance(scope, block.type, block.id);
    if (allowance?.effect === 'absent') return [];
    if (allowance?.effect === 'opaque') return [{ ...outline(block.id, block.type), opaque: true }];
    const { id, type } = block;
    const children = () => this.blocks(type, block.blocks, scope);

    switch (type) {
      case 'text':
      case '_announcement': {
        const text = htmlToText(setting(block, 'text', scope));
        return text ? [outline(id, type, { text })] : [];
      }
      case 'image': {
        const ref = setting(block, 'image', scope);
        const link = this.data.urls.resolve(setting(block, 'link', scope));
        return ref ? [outline(id, type, { images: [this.data.images.fromShopImage(ref).file], links: link ? [link] : [] })] : [];
      }
      case 'button': {
        const themeLabel = setting(block, 'label', scope).trim();
        const raw = setting(block, 'link', scope);
        const link = this.data.urls.resolve(raw);
        if (!themeLabel) return [];
        const label = allowanceWith(scope.template, type, id, 'replaced-label')?.label ?? themeLabel;
        if (allowanceWith(scope.template, type, id, 'fallback-link') && scope.product) {
          // Only an https link on orizen.com.au is used; anything else falls back to the ORIZEN search.
          const usable = link !== undefined && orizenUrl(link, scope.product.title) === link;
          return [outline(id, type, { text: label, links: usable ? [link] : ['*'] })];
        }
        return [outline(id, type, { text: label, links: link ? [link] : [] })];
      }
      case 'group': {
        const link = this.data.urls.resolve(setting(block, 'link', scope));
        return [outline(id, type, { links: link ? [link] : [], children: children() })];
      }
      case 'page-content': {
        const text = htmlToText(need(scope.page, 'a page').bodyHtml);
        return text ? [outline(id, type, { text })] : []; // an empty page body renders nothing
      }
      case 'product-title':
        return [outline(id, type, { text: need(scope.product, 'a product').title })];
      case 'collection-title':
        return [outline(id, type, { text: need(scope.collection, 'a collection').title })];
      case '_product-card-gallery': {
        const product = need(scope.product, 'a product');
        const files = product.images.map((image) => this.data.images.fromUrl(image.url).file);
        return [outline(id, type, { images: allowance?.effect === 'first-image-only' ? files.slice(0, 1) : files })];
      }
      case '_product-media-gallery': {
        const product = need(scope.product, 'a product');
        return [outline(id, type, { images: product.images.map((image) => this.data.images.fromUrl(image.url).file) })];
      }
      case '_collection-card-image': {
        const image = need(scope.collection, 'a collection').image;
        return [outline(id, type, { images: image ? [this.data.images.fromUrl(image.url).file] : [] })];
      }
      case '_accordion-row':
        return [outline(id, type, { text: setting(block, 'heading', scope).trim(), children: children() })];
      case 'disclosures': {
        // Shopify renders disclosures only from the product's `shopify.disclosure` metafield.
        const product = need(scope.product, 'a product');
        if (!product.metafields.some((m) => m.namespace === 'shopify' && m.key === 'disclosure')) return [];
        throw new Error(`product ${product.handle} has disclosures: the site has no rule for them yet`);
      }
      case '_header-logo': {
        const logo = this.data.logo ? [this.data.images.fromShopImage(this.data.logo).file] : [];
        return [outline(id, type, { images: logo, text: logo.length ? '' : this.data.shopName, links: ['/'] })];
      }
      case '_header-menu': {
        const menu = this.data.menus.find((m) => m.handle === stringSetting(block.settings, 'menu'));
        const { titles, links } = menuOutline(menu?.items ?? [], this.data);
        return [outline(id, type, { text: titles.join(' '), links })];
      }
      case 'footer-copyright':
        return [outline(id, type, { text: `© ${this.data.year}, ${this.data.shopName}` })];
      case '_product-details':
      case '_product-list-content':
      case 'accordion':
        return [outline(id, type, { children: children() })];
      case '_divider':
        return [outline(id, type)];
      default:
        throw new Error(`no fidelity rule for theme block type ${type} (${id})`);
    }
  }

  section(section: RawThemeSection, scope: Scope): OutlineNode {
    const { id, type, settings } = section;
    const image = (key: string) => {
      const ref = stringSetting(settings, key);
      return ref ? [this.data.images.fromShopImage(ref).file] : [];
    };
    switch (type) {
      case 'hero':
        return outline(id, type, { images: [...image('image_1'), ...image('image_1_mobile')], children: this.blocks(type, section.blocks, scope) });
      case 'section':
      case 'main-404':
        return outline(id, type, { images: image('background_image'), children: this.blocks(type, section.blocks, scope) });
      case 'main-page':
      case 'header-announcements':
      case 'header':
      case 'footer':
      case 'footer-utilities':
      case 'product-information':
      case 'product-recommendations':
        return outline(id, type, { children: this.blocks(type, section.blocks, scope) });
      case 'custom-liquid': {
        const html = stringSetting(settings, 'custom_liquid');
        return outline(id, type, customLiquidContent(html, this.data.images));
      }
      case 'product-list': {
        const products = this.productsOf(stringSetting(settings, 'collection'), numberSetting(settings, 'max_products'));
        return outline(id, type, {
          children: this.listBlocks(section, scope, (card) => products.map((p) => this.productCard(card, p, scope))),
        });
      }
      case 'main-collection': {
        const collection = need(scope.collection, 'a collection');
        const products = this.productsOf(collection.handle, numberSetting(settings, 'products_per_page'));
        return outline(id, type, {
          children: this.listBlocks(section, scope, (card) => products.map((p) => this.productCard(card, p, scope))),
        });
      }
      case 'collection-list': {
        const handles = Array.isArray(settings['collection_list']) ? (settings['collection_list'] as string[]) : [];
        const limit = numberSetting(settings, 'max_collections');
        const collections = handles
          .flatMap((h) => this.data.collections.filter((c) => c.handle === h))
          .slice(0, limit ?? handles.length);
        return outline(id, type, {
          children: this.listBlocks(section, scope, (card) =>
            collections.map((collection) => {
              const url = this.data.urls.collection(collection.handle);
              return outline(card.id, card.type, {
                links: url ? [url] : [],
                children: this.blocks(card.type, card.blocks, { ...scope, collection }),
              });
            }),
          ),
        });
      }
      default:
        throw new Error(`no fidelity rule for theme section type ${type} (${id})`);
    }
  }
}

/** Text, images and links of an owner-written custom-liquid section (styles and scripts dropped). */
function customLiquidContent(html: string, images: ImageResolver): Partial<OutlineNode> {
  const document = parse(`<!doctype html><html><body>${html}</body></html>`);
  const body = findBody(document.childNodes);
  const collected = collect(body ? body.childNodes : [], () => false, (el) => attr(el, 'src'));
  return {
    text: collected.text,
    images: collected.images.map((src) => images.fromUrl(src).file),
    links: collected.links.filter((href) => /^https?:\/\//.test(href)),
  };
}

/** The theme outline of a template or section group on one page. */
export function expectedOutline(sections: readonly RawThemeSection[], scope: Scope, data: FidelityData): OutlineNode[] {
  const builder = new ExpectedBuilder(data);
  return enabled(sections).map((section) => builder.section(section, scope));
}

// ---------------------------------------------------------------------------------------------
// The site side: the outline read back from built HTML.

const SKIPPED_ELEMENTS = new Set(['script', 'style', 'template', 'noscript', 'head']);
const BLOCK_ELEMENTS = new Set([
  'p', 'div', 'section', 'article', 'header', 'footer', 'main', 'nav', 'ul', 'ol', 'li', 'br', 'hr',
  'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'details', 'summary', 'figure', 'figcaption', 'blockquote', 'picture',
]);

function isElement(node: Node): node is Element {
  return 'tagName' in node;
}

function attr(element: Element, name: string): string | undefined {
  return element.attrs.find((a) => a.name === name)?.value;
}

function findBody(nodes: readonly Node[]): Element | undefined {
  for (const node of nodes) {
    if (!isElement(node)) continue;
    if (node.tagName === 'body') return node;
    const found = findBody(node.childNodes);
    if (found) return found;
  }
  return undefined;
}

interface Collected {
  text: string;
  images: string[];
  links: string[];
}

/**
 * Text, images and links under `nodes`, stopping at elements `stop` claims (child outline nodes or
 * allowed markup). Images are named by `data-image` (the export file the component rendered).
 */
function collect(
  nodes: readonly Node[],
  stop: (el: Element) => boolean,
  imageOf: (el: Element) => string | undefined = (el) =>
    attr(el, 'data-image') ?? (el.tagName === 'img' ? `unmarked:${attr(el, 'src') ?? ''}` : undefined),
): Collected {
  const parts: string[] = [];
  const images: string[] = [];
  const links: string[] = [];
  const walk = (list: readonly Node[]) => {
    for (const node of list) {
      if (node.nodeName === '#text' && 'value' in node) {
        parts.push(node.value);
        continue;
      }
      if (!isElement(node) || SKIPPED_ELEMENTS.has(node.tagName) || stop(node)) continue;
      if (node.tagName === 'img' || node.tagName === 'source') {
        const file = imageOf(node);
        if (file && !images.includes(file)) images.push(file);
      }
      if (node.tagName === 'a') {
        const href = attr(node, 'href');
        if (href !== undefined) links.push(href);
      }
      const block = BLOCK_ELEMENTS.has(node.tagName);
      if (block) parts.push(' ');
      walk(node.childNodes);
      if (block) parts.push(' ');
    }
  };
  walk(nodes);
  return { text: parts.join('').replace(/[\s ]+/g, ' ').trim(), images, links };
}

export interface RenderedOutline {
  nodes: OutlineNode[];
  /** Content outside any theme node, and unknown allowance keys. */
  problems: string[];
}

const isOutlineElement = (el: Element) => attr(el, 'data-theme-section') !== undefined || attr(el, 'data-theme-block') !== undefined;

/** The outline the built page renders, read from its data attributes. */
export function renderedOutline(html: string): RenderedOutline {
  const body = findBody(parse(html).childNodes);
  const problems: string[] = [];
  const unknownKeys = new Set<string>();
  const allowed = (el: Element) => {
    const key = attr(el, 'data-allowed');
    if (key === undefined) return false;
    if (!(key in ALLOWED_DIFFERENCES)) unknownKeys.add(key);
    return true;
  };
  const stop = (el: Element) => allowed(el) || isOutlineElement(el);

  const nodesUnder = (list: readonly Node[]): OutlineNode[] => {
    const found: OutlineNode[] = [];
    for (const node of list) {
      if (!isElement(node) || SKIPPED_ELEMENTS.has(node.tagName) || allowed(node)) continue;
      if (isOutlineElement(node)) found.push(toNode(node));
      else found.push(...nodesUnder(node.childNodes));
    }
    return found;
  };
  const toNode = (el: Element): OutlineNode => {
    const own = collect(el.childNodes, stop);
    const links = [...own.links];
    if (el.tagName === 'a') {
      const href = attr(el, 'href');
      if (href !== undefined) links.unshift(href);
    }
    const images = [...own.images];
    if (el.tagName === 'img') {
      const file = attr(el, 'data-image');
      if (file) images.unshift(file);
    }
    return {
      id: attr(el, 'data-theme-section') ?? attr(el, 'data-theme-block') ?? '',
      type: attr(el, 'data-theme-type') ?? '',
      text: own.text,
      images,
      links,
      children: nodesUnder(el.childNodes),
    };
  };

  const children = body ? body.childNodes : [];
  const nodes = nodesUnder(children);
  const outside = collect(children, stop);
  if (outside.text) problems.push(`content outside any theme section: text "${outside.text}"`);
  if (outside.images.length) problems.push(`content outside any theme section: images ${outside.images.join(', ')}`);
  if (outside.links.length) problems.push(`content outside any theme section: links ${outside.links.join(', ')}`);
  for (const key of unknownKeys) problems.push(`data-allowed="${key}" is not in ALLOWED_DIFFERENCES`);
  return { nodes, problems };
}

// ---------------------------------------------------------------------------------------------
// Comparing the two.

const squash = (text: string) => text.replace(/[\s ]+/g, '');
/** Repeated cards share the theme's card id: they are told apart by the item they link to. */
const isCard = (node: OutlineNode) => node.type === '_product-card' || node.type === '_collection-card';
const keyOf = (node: OutlineNode) => `${node.id}[${node.type}]${isCard(node) ? (node.links[0] ?? '') : ''}`;
const label = (node: OutlineNode) => `${node.id} [${node.type}]`;

function sameKey(a: OutlineNode, b: OutlineNode): boolean {
  if (a.id !== b.id || a.type !== b.type) return false;
  return a.links[0] === '*' || a.opaque === true || keyOf(a) === keyOf(b);
}

/** Longest common subsequence of sibling lists, by id, type and (for cards) link. */
function align(expected: readonly OutlineNode[], actual: readonly OutlineNode[]): Array<[number, number]> {
  const rows = expected.length;
  const cols = actual.length;
  const table: number[][] = Array.from({ length: rows + 1 }, () => new Array<number>(cols + 1).fill(0));
  for (let i = rows - 1; i >= 0; i -= 1) {
    for (let j = cols - 1; j >= 0; j -= 1) {
      const row = table[i] as number[];
      row[j] = sameKey(expected[i] as OutlineNode, actual[j] as OutlineNode)
        ? (table[i + 1]?.[j + 1] ?? 0) + 1
        : Math.max(table[i + 1]?.[j] ?? 0, row[j + 1] ?? 0);
    }
  }
  const pairs: Array<[number, number]> = [];
  let i = 0;
  let j = 0;
  while (i < rows && j < cols) {
    if (sameKey(expected[i] as OutlineNode, actual[j] as OutlineNode)) {
      pairs.push([i, j]);
      i += 1;
      j += 1;
    } else if ((table[i + 1]?.[j] ?? 0) >= (table[i]?.[j + 1] ?? 0)) i += 1;
    else j += 1;
  }
  return pairs;
}

function compareContent(path: string, expected: OutlineNode, actual: OutlineNode): string[] {
  if (expected.opaque) return [];
  const problems: string[] = [];
  if (squash(expected.text) !== squash(actual.text)) {
    problems.push(`theme ${path} [${expected.type}] text "${expected.text}", site has text "${actual.text}"`);
  }
  // A hero's desktop and mobile crops come in either order: compare images as a set.
  if ([...expected.images].sort().join('|') !== [...actual.images].sort().join('|')) {
    problems.push(`theme ${path} [${expected.type}] images [${expected.images.join(', ')}], site has images [${actual.images.join(', ')}]`);
  }
  const linksMatch = expected.links[0] === '*' ? actual.links.length === 1 : expected.links.join('|') === actual.links.join('|');
  if (!linksMatch) {
    problems.push(`theme ${path} [${expected.type}] links [${expected.links.join(', ')}], site has links [${actual.links.join(', ')}]`);
  }
  return problems;
}

/** Differences between the theme outline and the site outline, one line each; empty when they match. */
export function compareOutlines(expected: readonly OutlineNode[], actual: readonly OutlineNode[], parent = ''): string[] {
  const problems: string[] = [];
  const pairs = align(expected, actual);
  let e = 0;
  let a = 0;
  const flush = (untilE: number, untilA: number) => {
    for (; a < untilA; a += 1) {
      const node = actual[a] as OutlineNode;
      const previous = a > 0 ? (actual[a - 1] as OutlineNode).id : undefined;
      const where = previous ? ` after ${parent ? `${parent} > ` : ''}${previous}` : parent ? ` in ${parent}` : '';
      problems.push(`site renders ${label(node)}${where}, the theme has no such node here`);
    }
    for (; e < untilE; e += 1) {
      const node = expected[e] as OutlineNode;
      problems.push(`theme ${parent ? `${parent} > ` : ''}${label(node)} is missing on the site`);
    }
  };
  for (const [i, j] of pairs) {
    flush(i, j);
    const exp = expected[i] as OutlineNode;
    const act = actual[j] as OutlineNode;
    const path = parent ? `${parent} > ${exp.id}` : exp.id;
    problems.push(...compareContent(path, exp, act));
    if (!exp.opaque) problems.push(...compareOutlines(exp.children, act.children, path));
    e = i + 1;
    a = j + 1;
  }
  flush(expected.length, actual.length);
  return problems;
}
