/**
 * Every intended difference between the Shopify theme and the static site, in one place, each
 * with its reason. The fidelity check (`fidelity.ts`) is strict for everything not listed here,
 * and the site model (`theme.ts`) reads the same entries, so a difference exists only if it is
 * written down here. Site-only markup is marked in the HTML with `data-allowed="<key>"`.
 */
import { siteConfig } from '../site.config.ts';

export type ThemeEffect =
  /** The theme renders it; the site does not. */
  | 'absent'
  /** The site renders its own substitute in the node's place: id and type are checked, content is not. */
  | 'opaque'
  /** The site's own link rule (ORIZEN): when the theme's link is not usable the site's link is not checked; a usable one must match. */
  | 'fallback-link'
  /** The site shows `label` instead of the theme's label. */
  | 'replaced-label'
  /** The theme can swipe through all product media; the site shows the featured image. */
  | 'first-image-only'
  /** Rendered by the theme from section settings, not blocks: no outline node either side. */
  | 'not-modelled';

export interface AllowedDifference {
  reason: string;
  /** The theme nodes this covers. Absent for site-only markup, marked `data-allowed="<key>"`. */
  theme?: { effect: ThemeEffect; type?: string; template?: string; id?: string; label?: string };
}

/** Every intended difference between the theme and the site, in one place. */
export const ALLOWED_DIFFERENCES = {
  price: {
    reason: 'siteConfig.showPrices is false: the site presents the products and does not sell them, so no price is shown.',
    theme: { effect: 'absent', type: 'price' },
  },
  'product-card-media': {
    reason: 'A theme product card can swipe through every product image; the static card shows the featured (first) image.',
    theme: { effect: 'first-image-only', type: '_product-card-gallery' },
  },
  'contact-form': {
    reason: 'A static site cannot submit a Shopify contact form; the site shows an email link to the enquiry address in its place.',
    theme: { effect: 'opaque', type: 'contact-form' },
  },
  'product-recommendations': {
    reason:
      'Shopify computes product recommendations per request; the static site cannot. In their place it lists up to 4 other products of the same collection (marked data-allowed="related-products").',
    theme: { effect: 'absent', type: '_product-card', template: 'templates/product.product-catalogue.json' },
  },
  'shop-now-fallback': {
    reason:
      'Owner decision 2026-11-07: every product links to ORIZEN. The theme button reads custom.orizen_product_url; when it is empty the site links to an ORIZEN search for the product instead of a dead button.',
    theme: { effect: 'fallback-link', type: 'button', template: 'templates/product.product-catalogue.json', id: 'button_z9EEwn' },
  },
  'shop-now-label': {
    reason:
      'Owner decision 2026-11-07: the ORIZEN button on product pages reads siteConfig.orizenButtonLabel ("Shop in Orizen.com.au") instead of the theme label "SHOP NOW".',
    theme: {
      effect: 'replaced-label',
      type: 'button',
      template: 'templates/product.product-catalogue.json',
      id: 'button_z9EEwn',
      label: siteConfig.orizenButtonLabel,
    },
  },
  'social-links': {
    reason:
      'The theme footer holds only placeholder URLs (facebook.com, instagram.com ... home pages, no Rosart profiles); the site leaves them out until the owner sets real ones.',
    theme: { effect: 'absent', type: 'social-links' },
  },
  'policy-links': {
    reason: 'The footer policy list links to Shopify shop policies; the export holds no policies, so the site has none to link.',
    theme: { effect: 'absent', type: 'footer-policy-list' },
  },
  'header-commerce': {
    reason:
      'The theme header also shows the cart, customer account and country/language selectors (section settings, not blocks): a static site has no cart, accounts or markets.',
    theme: { effect: 'not-modelled', type: 'header' },
  },
  'all-products-order': {
    reason:
      "A list bound to Shopify's virtual `all` collection shows active products; the export holds no order for `all`, so the site uses the export order.",
    theme: { effect: 'not-modelled', type: 'product-list' },
  },
  'skip-link': {
    reason: 'Keyboard users get a "Skip to content" link before the header (accessibility; visually hidden until focused).',
  },
  'menu-toggle': {
    reason: 'The mobile menu button and submenu buttons carry their own labels (the theme has equivalent controls).',
  },
  'screen-reader-h1': {
    reason: 'A page whose theme title section is disabled still gets a visually hidden h1 with the page title, for screen readers and search engines.',
  },
  'related-products': {
    reason: 'The substitute for the product recommendations (see product-recommendations).',
  },
  'external-link-note': {
    reason: 'A visually hidden "(name, external site)" note on links that leave the site, for screen readers.',
  },
} as const satisfies Record<string, AllowedDifference>;

export type AllowedKey = keyof typeof ALLOWED_DIFFERENCES;

export type ThemeAllowance = NonNullable<AllowedDifference['theme']>;

/** Every allowance covering a theme block or section on a template. */
export function allowancesFor(template: string, type: string, id: string): ThemeAllowance[] {
  const found: ThemeAllowance[] = [];
  for (const entry of Object.values(ALLOWED_DIFFERENCES) as AllowedDifference[]) {
    const theme = entry.theme;
    if (!theme || theme.effect === 'not-modelled') continue;
    if (theme.type !== undefined && theme.type !== type) continue;
    if (theme.template !== undefined && theme.template !== template) continue;
    if (theme.id !== undefined && theme.id !== id) continue;
    found.push(theme);
  }
  return found;
}

/** The allowance with this effect on a theme block or section, if any. */
export function allowanceWith(template: string, type: string, id: string, effect: ThemeEffect): ThemeAllowance | undefined {
  return allowancesFor(template, type, id).find((a) => a.effect === effect);
}

/** The first allowance covering a theme block or section on a template, if any. */
export function allowanceFor(template: string, type: string, id: string): ThemeAllowance | undefined {
  return allowancesFor(template, type, id)[0];
}
