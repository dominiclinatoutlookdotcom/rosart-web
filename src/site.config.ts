/**
 * Every site setting in one place. Content (copy, products, images) comes from `export/`; this
 * file holds only the choices the export does not make.
 */
import { resolveSiteUrl } from './lib/commerce.ts';

export const siteConfig = {
  /** Canonical origin for canonical tags, Open Graph, the sitemap and robots.txt. Env `SITE_URL` overrides. */
  siteUrl: resolveSiteUrl(process.env['SITE_URL']),

  /** Where the product "Enquire" button and the contact page send email. */
  enquiryEmail: 'info@rosart.com.au',

  /** Show product prices (AUD, from the export). Off: the site presents products, it does not sell them. */
  showPrices: false,

  /**
   * The header announcement. `undefined` uses the theme's text ("From Bulgaria's Valley of
   * Roses, with Care"); a string replaces it; an empty string hides the bar.
   */
  announcement: undefined as string | undefined,

  /**
   * Alt text for exported images that Shopify holds none for (no image in the store has alt text).
   * Only images that carry words or meaning are listed; the rest are decorative (`alt=""`), and
   * product images fall back to the product title. Keyed by file name in `export/images/`.
   */
  imageAlt: {
    'ESPRIT_HONG_KONG.png': 'Rosart',
    'Add_a_heading_fcec8aa5-9bda-44ce-96bc-7966b68e00f2.png': 'Our Story',
    'USDA_and_EU_Organic_Certifications.png':
      'USDA and EU Organic Certification: Ceres certified, EU organic, USDA Organic and GMP certified',
    'Rosart_Distillery_in_Bulgaria_s_Valley_of_Roses.png':
      "Map, Our Roots in Bulgaria: Rosart's organic rose fields and distillery in Zlatosel, in the heart of Bulgaria's Valley of Roses",
    'Rosart_s_Bulgarian_Valley_of_Roses_Map.png':
      "Map, Our Roots in Bulgaria: Rosart's organic rose fields and distillery in Zlatosel, in the heart of Bulgaria's Valley of Roses",
  } as Readonly<Record<string, string>>,
} as const;
