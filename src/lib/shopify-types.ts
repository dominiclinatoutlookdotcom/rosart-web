/** Shapes of the read-only Shopify export in `export/` (written by `tools/export_shopify.py`). */

export interface RawImage {
  altText: string | null;
  url: string;
  width: number | null;
  height: number | null;
}

export interface RawMetafield {
  namespace: string;
  key: string;
  type: string;
  value: string;
}

export interface RawSeo {
  title: string | null;
  description: string | null;
}

export interface RawProduct {
  handle: string;
  /** The product template suffix: `product-catalogue` renders with `templates/product.product-catalogue.json`. */
  templateSuffix?: string | null;
  title: string;
  status: string;
  descriptionHtml: string;
  productType: string;
  vendor: string;
  tags: string[];
  seo: RawSeo;
  images: RawImage[];
  metafields: RawMetafield[];
  variants: Array<{ price: string; sku: string | null; compareAtPrice: string | null }>;
}

export interface RawCollection {
  handle: string;
  templateSuffix?: string | null;
  title: string;
  descriptionHtml: string;
  image: RawImage | null;
  productHandles: string[];
  seo: RawSeo;
}

export interface RawPage {
  handle: string;
  templateSuffix?: string | null;
  title: string;
  bodyHtml: string;
  isPublished: boolean;
  seo: RawSeo;
}

export interface RawMenuItem {
  title: string;
  type: string;
  url: string | null;
  resourceHandle: string | null;
  items: RawMenuItem[];
}

export interface RawMenu {
  handle: string;
  title: string;
  items: RawMenuItem[];
}

export interface RawShop {
  name: string;
  currencyCode: string;
  myshopifyDomain: string;
  primaryDomain: { host: string; url: string };
}

export interface RawFile {
  filename: string;
  alt: string | null;
  url: string;
}

export interface RawManifest {
  images: Record<string, { url: string; bytes: number; sha256: string; references: string[] }>;
  shop_images: Record<string, string>;
  sources: Record<string, string>;
}

export interface RawThemeBlock {
  id: string;
  type: string;
  disabled: boolean;
  static?: boolean;
  settings: Record<string, unknown>;
  /** Content settings the theme fills with Liquid from the page's data (`{{ closest.product.title }}`). */
  bindings?: Record<string, string>;
  blocks: RawThemeBlock[];
}

export interface RawThemeSection {
  id: string;
  type: string;
  disabled: boolean;
  settings: Record<string, unknown>;
  bindings?: Record<string, string>;
  blocks: RawThemeBlock[];
}

export interface RawThemeContent {
  global_settings: Record<string, unknown>;
  section_groups: Record<string, { type: string; sections: RawThemeSection[] }>;
  templates: Record<string, { sections: RawThemeSection[] }>;
}

export interface RawExport {
  shop: RawShop;
  products: RawProduct[];
  collections: RawCollection[];
  pages: RawPage[];
  menus: RawMenu[];
  files: RawFile[];
  manifest: RawManifest;
  theme: RawThemeContent;
}
