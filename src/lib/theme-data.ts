/**
 * How the theme's JSON maps to what a page shows, shared by the site model (`theme.ts`) and the
 * fidelity check (`fidelity.ts`): where static blocks render, and the data a Liquid binding
 * (`{{ closest.product.title }}`) names. Facts about the theme's structure, not its code.
 */
import { metafieldToHtml } from './richtext.ts';
import type { RawCollection, RawPage, RawProduct, RawThemeBlock } from './shopify-types.ts';

/**
 * Parents whose Liquid renders their static blocks before the ordered ones (a product card's
 * image before its title, the product gallery and details before extra blocks, a product list's
 * header and cards before extra blocks). Everywhere else static blocks come after.
 */
const STATIC_FIRST = new Set(['_collection-card', 'product-information', 'product-list', 'main-collection']);

/** Enabled blocks in the order the theme renders them. */
export function orderedBlocks(parentType: string, blocks: readonly RawThemeBlock[]): RawThemeBlock[] {
  const enabled = blocks.filter((b) => !b.disabled);
  if (!STATIC_FIRST.has(parentType)) return enabled;
  return [...enabled.filter((b) => b.static), ...enabled.filter((b) => !b.static)];
}

/** What `closest.*` points at on a page. */
export interface BindingScope {
  page?: RawPage;
  product?: RawProduct;
  collection?: RawCollection;
}

function need<T>(value: T | undefined, what: string, expression: string): T {
  if (value === undefined) throw new Error(`theme binding {{ ${expression} }} needs ${what}, which this page does not have`);
  return value;
}

function metafield(product: RawProduct, namespace: string, key: string) {
  return product.metafields.find((m) => m.namespace === namespace && m.key === key);
}

/** The raw value (HTML or text) one Liquid expression shows. Unknown expressions throw: no guessing. */
function resolveExpression(expression: string, filters: string, scope: BindingScope): string {
  const custom = /^closest\.product\.metafields\.custom\.([a-z0-9_]+)(\.value)?$/.exec(expression);
  if (custom) {
    const field = metafield(need(scope.product, 'a product', expression), 'custom', custom[1] ?? '');
    if (!field) return '';
    if (!custom[2] && filters.includes('metafield_tag')) return metafieldToHtml(field);
    return field.value;
  }
  switch (expression) {
    case 'closest.page.title':
      return need(scope.page, 'a page', expression).title;
    case 'closest.page.content':
      return need(scope.page, 'a page', expression).bodyHtml;
    case 'closest.product.title':
      return need(scope.product, 'a product', expression).title;
    case 'closest.product.description':
      return need(scope.product, 'a product', expression).descriptionHtml;
    case 'closest.product':
      return need(scope.product, 'a product', expression).handle;
    case 'closest.collection.title':
      return need(scope.collection, 'a collection', expression).title;
    case 'closest.collection.description':
      return need(scope.collection, 'a collection', expression).descriptionHtml;
    default:
      throw new Error(`no rule for the theme binding {{ ${expression} }}`);
  }
}

/** A setting value with every `{{ ... }}` replaced by the data it names. */
export function resolveBindings(value: string, scope: BindingScope): string {
  return value.replace(/\{\{\s*([^}|]+?)\s*((?:\|[^}]*)?)\}\}/g, (_, expression: string, filters: string) =>
    resolveExpression(expression.trim(), filters, scope),
  );
}

/** The metafield key a binding reads (`custom.how_to_use` for `{{ closest.product.metafields.custom.how_to_use }}`). */
export function boundMetafieldKey(value: string): string | undefined {
  return /closest\.product\.metafields\.custom\.([a-z0-9_]+)/.exec(value)?.[1];
}

/** A content setting as the theme shows it: the Liquid binding when it has one, else the plain value. */
export function themeSetting(
  node: { settings: Record<string, unknown>; bindings?: Record<string, string> },
  key: string,
  scope: BindingScope,
): string {
  const bound = node.bindings?.[key];
  if (bound !== undefined) return resolveBindings(bound, scope);
  const value = node.settings[key];
  return typeof value === 'string' ? value : '';
}
