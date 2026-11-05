/**
 * Reads the owner's hand-written "Our Global Connections" block (a custom-liquid section on the
 * Our Story page) into structured content, so the site renders it with its own markup and local,
 * optimised logos instead of the original inline HTML, CSS and CDN images.
 */
import { parseFragment, type DefaultTreeAdapterTypes } from 'parse5';
import type { ImageRef, ImageResolver } from './images.ts';

type Node = DefaultTreeAdapterTypes.ChildNode;
type Element = DefaultTreeAdapterTypes.Element;

export interface Connection {
  role: string;
  name: string;
  logo: ImageRef;
  link: { label: string; href: string } | null;
}

export interface Connections {
  heading: string;
  intro: string;
  items: Connection[];
}

function isElement(node: Node): node is Element {
  return 'tagName' in node;
}

function attr(element: Element, name: string): string | undefined {
  return element.attrs.find((a) => a.name === name)?.value;
}

function hasClass(element: Element, name: string): boolean {
  return (attr(element, 'class') ?? '').split(/\s+/).includes(name);
}

function findAll(nodes: readonly Node[], match: (el: Element) => boolean, out: Element[] = []): Element[] {
  for (const node of nodes) {
    if (!isElement(node)) continue;
    if (match(node)) out.push(node);
    findAll(node.childNodes, match, out);
  }
  return out;
}

function first(root: Element | readonly Node[], match: (el: Element) => boolean): Element | undefined {
  return findAll(Array.isArray(root) ? root : (root as Element).childNodes, match)[0];
}

function textOf(element: Element | undefined): string {
  if (!element) return '';
  const parts: string[] = [];
  const walk = (nodes: readonly Node[]) => {
    for (const node of nodes) {
      if (node.nodeName === '#text' && 'value' in node) parts.push(node.value);
      else if (isElement(node)) walk(node.childNodes);
    }
  };
  walk(element.childNodes);
  return parts.join('').replace(/[\s ]+/g, ' ').trim();
}

/** Returns null when the HTML is not the connections layout. */
export function parseConnections(html: string, images: ImageResolver): Connections | null {
  const nodes = parseFragment(html).childNodes;
  const root = first(nodes, (el) => hasClass(el, 'rosart-connections'));
  if (!root) return null;
  const cards = findAll(root.childNodes, (el) => hasClass(el, 'rosart-connection'));
  const heading = textOf(first(root, (el) => el.tagName === 'h2'));
  if (!heading || cards.length === 0) return null;

  const items = cards.map((card): Connection => {
    const img = first(card, (el) => el.tagName === 'img');
    const src = img ? attr(img, 'src') : undefined;
    if (!img || !src) throw new Error('A connection card has no logo image');
    const name = textOf(first(card, (el) => el.tagName === 'h3'));
    const anchor = first(card, (el) => el.tagName === 'a');
    const href = anchor ? attr(anchor, 'href')?.trim() : undefined;
    return {
      role: textOf(first(card, (el) => hasClass(el, 'role'))),
      name,
      logo: images.fromUrl(src, attr(img, 'alt'), name),
      link: anchor && href && /^https?:\/\//.test(href) ? { label: textOf(anchor), href } : null,
    };
  });

  return { heading, intro: textOf(first(root, (el) => hasClass(el, 'intro'))), items };
}
