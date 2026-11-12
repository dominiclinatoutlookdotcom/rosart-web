/**
 * Turns owner-written Shopify HTML (product descriptions, page bodies, theme text settings) into
 * a small, safe subset: an allowlist of formatting tags, no attributes except resolved link
 * targets, every text node re-escaped. Images, scripts, styles and forms never survive, so no
 * store CDN reference leaks into the site.
 */
import { parseFragment, type DefaultTreeAdapterTypes } from 'parse5';

type Node = DefaultTreeAdapterTypes.ChildNode;
type Element = DefaultTreeAdapterTypes.Element;

export interface SanitizeOptions {
  /** Maps a link target to a site URL; return undefined to keep only the link text. */
  resolveLink?: (href: string) => string | undefined;
}

const RENAMED: Readonly<Record<string, string>> = { b: 'strong', i: 'em' };
const KEPT = new Set(['p', 'br', 'strong', 'em', 'u', 'a', 'ul', 'ol', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote']);
/** Elements dropped together with everything inside them. */
const DROPPED = new Set([
  'script', 'style', 'link', 'meta', 'noscript', 'template', 'iframe', 'object', 'embed', 'form', 'input',
  'button', 'select', 'textarea', 'img', 'picture', 'video', 'audio', 'svg', 'canvas', 'head', 'title',
]);
const VOID = new Set(['br']);
/** Elements removed when they hold no visible text. */
const DROP_IF_EMPTY = new Set(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'ul', 'ol', 'strong', 'em', 'u', 'blockquote', 'a']);
const BLOCK = new Set(['p', 'div', 'li', 'ul', 'ol', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'br', 'tr', 'section']);

const ESCAPES: Readonly<Record<string, string>> = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

export function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (c) => ESCAPES[c] ?? c);
}

/** The default link policy: site-relative paths, http(s), mailto and tel; nothing else. */
export function safeHref(href: string): string | undefined {
  const value = href.trim();
  if (/^\/(?!\/)/.test(value) || /^#/.test(value)) return value;
  if (/^(https?:\/\/|mailto:|tel:)/i.test(value)) return value;
  return undefined;
}

function isExternal(href: string): boolean {
  return /^https?:\/\//i.test(href);
}

function attr(element: Element, name: string): string | undefined {
  return element.attrs.find((a) => a.name === name)?.value;
}

function isElement(node: Node): node is Element {
  return 'tagName' in node;
}

function isBlank(html: string): boolean {
  return html.replace(/<br>/g, '').replace(/&nbsp;| /g, '').trim() === '';
}

function renderNodes(nodes: readonly Node[], options: SanitizeOptions): string {
  return nodes.map((node) => renderNode(node, options)).join('');
}

function renderNode(node: Node, options: SanitizeOptions): string {
  if (node.nodeName === '#text' && 'value' in node) return escapeHtml(node.value);
  if (!isElement(node)) return '';

  const tag = RENAMED[node.tagName] ?? node.tagName;
  if (DROPPED.has(tag)) return '';
  if (VOID.has(tag)) return `<${tag}>`;

  let inner = renderNodes(node.childNodes, options);
  if (!KEPT.has(tag)) return inner;
  if (BLOCK.has(tag)) inner = inner.replace(/^\s+|\s+$/g, '');
  if (DROP_IF_EMPTY.has(tag) && isBlank(inner)) return '';

  if (tag === 'a') {
    const raw = attr(node, 'href');
    const resolve = options.resolveLink ?? safeHref;
    const resolved = raw === undefined ? undefined : resolve(raw);
    const href = resolved === undefined ? undefined : safeHref(resolved);
    if (href === undefined) return inner;
    const rel = isExternal(href) ? ' rel="noopener noreferrer"' : '';
    return `<a href="${escapeHtml(href)}"${rel}>${inner}</a>`;
  }
  return `<${tag}>${inner}</${tag}>`;
}

export function sanitizeHtml(html: string, options: SanitizeOptions = {}): string {
  return renderNodes(parseFragment(html).childNodes, options).trim();
}

/**
 * The link targets the sanitised HTML keeps, in document order: exactly the `<a href>`s `sanitizeHtml`
 * renders for the same input and options (a refused link, or one with no text, is absent).
 */
export function htmlLinks(html: string, options: SanitizeOptions = {}): string[] {
  const links: string[] = [];
  const walk = (nodes: readonly Node[]): void => {
    for (const node of nodes) {
      if (!isElement(node)) continue;
      if (node.tagName === 'a') {
        const href = attr(node, 'href');
        if (href !== undefined) links.push(href);
      }
      walk(node.childNodes);
    }
  };
  walk(parseFragment(sanitizeHtml(html, options)).childNodes);
  return links;
}

function collectText(nodes: readonly Node[], out: string[]): void {
  for (const node of nodes) {
    if (node.nodeName === '#text' && 'value' in node) {
      out.push(node.value);
    } else if (isElement(node) && !DROPPED.has(node.tagName)) {
      const block = BLOCK.has(node.tagName);
      if (block) out.push(' ');
      collectText(node.childNodes, out);
      if (block) out.push(' ');
    }
  }
}

/** Plain text of an HTML fragment: entities decoded, whitespace (including nbsp) collapsed. */
export function htmlToText(html: string): string {
  const out: string[] = [];
  collectText(parseFragment(html).childNodes, out);
  return out.join('').replace(/[\s ]+/g, ' ').trim();
}
