/**
 * Renders Shopify metafield values to safe HTML: the rich text JSON of `rich_text_field`
 * metafields, and the plain text of `*_text_field` metafields. All text is escaped; links go
 * through the caller's resolver (the URL map) and a scheme allowlist.
 */
import { escapeHtml, safeHref } from './html.ts';

export interface RichTextNode {
  type: string;
  children?: RichTextNode[];
  value?: string;
  bold?: boolean;
  italic?: boolean;
  level?: number;
  listType?: string;
  url?: string;
  title?: string;
}

export interface RichTextOptions {
  resolveLink?: (href: string) => string | undefined;
  /** The highest heading level content may use; the page owns the levels above it. Default 3. */
  minHeadingLevel?: number;
}

function parse(value: string): RichTextNode {
  let parsed: unknown;
  try {
    parsed = JSON.parse(value);
  } catch (error) {
    throw new Error(`Not valid Shopify rich text JSON: ${(error as Error).message}`);
  }
  if (typeof parsed !== 'object' || parsed === null || typeof (parsed as RichTextNode).type !== 'string') {
    throw new Error('Not valid Shopify rich text JSON: no root node');
  }
  return parsed as RichTextNode;
}

function renderText(node: RichTextNode): string {
  let html = escapeHtml(node.value ?? '');
  if (node.italic) html = `<em>${html}</em>`;
  if (node.bold) html = `<strong>${html}</strong>`;
  return html;
}

function render(node: RichTextNode, options: Required<Pick<RichTextOptions, 'minHeadingLevel'>> & RichTextOptions): string {
  const children = (node.children ?? []).map((child) => render(child, options)).join('');
  switch (node.type) {
    case 'root':
      return children;
    case 'text':
      return renderText(node);
    case 'paragraph':
      return `<p>${children}</p>`;
    case 'heading': {
      const level = Math.min(6, Math.max(options.minHeadingLevel, Math.trunc(node.level ?? 6)));
      return `<h${level}>${children}</h${level}>`;
    }
    case 'list': {
      const tag = node.listType === 'ordered' ? 'ol' : 'ul';
      return `<${tag}>${children}</${tag}>`;
    }
    case 'list-item':
      return `<li>${children}</li>`;
    case 'link': {
      const resolved = node.url ? (options.resolveLink ?? safeHref)(node.url) : undefined;
      const href = resolved === undefined ? undefined : safeHref(resolved);
      if (href === undefined) return children;
      const title = node.title ? ` title="${escapeHtml(node.title)}"` : '';
      const rel = /^https?:\/\//i.test(href) ? ' rel="noopener noreferrer"' : '';
      return `<a href="${escapeHtml(href)}"${title}${rel}>${children}</a>`;
    }
    default:
      return children;
  }
}

export function richTextToHtml(value: string | RichTextNode, options: RichTextOptions = {}): string {
  const root = typeof value === 'string' ? parse(value) : value;
  return render(root, { minHeadingLevel: 3, ...options });
}

/** Plain multi-line text: a paragraph per blank-line block, a `<br>` per single newline. */
export function multilineToHtml(text: string): string {
  return text
    .replace(/\r\n?/g, '\n')
    .split(/\n\s*\n/)
    .map((block) => block.trim())
    .filter(Boolean)
    .map((block) => `<p>${block.split('\n').map((line) => escapeHtml(line.trim())).join('<br>')}</p>`)
    .join('');
}

export function metafieldToHtml(metafield: { type: string; value: string }, options: RichTextOptions = {}): string {
  switch (metafield.type) {
    case 'rich_text_field':
      return richTextToHtml(metafield.value, options);
    case 'multi_line_text_field':
    case 'single_line_text_field':
    case 'string':
      return multilineToHtml(metafield.value);
    default:
      throw new Error(`Cannot render a metafield of type ${metafield.type}`);
  }
}
