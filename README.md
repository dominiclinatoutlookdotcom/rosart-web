# rosart-web

Rosart is an organic Bulgarian rose skincare brand. Its Shopify store
(`rosart-mhfmyuxq.myshopify.com`) is being retired and rebuilt as a static Astro site on S3.

This repo holds two things:

- a **read-only content export** of that store (`export/`): the store's content as JSON plus
  every image it uses. Once Shopify closes, `export/` is the site's only copy of the content and
  images;
- the **static website** (Astro, `src/`), built from that export into plain files in `dist/` for a
  private S3 bucket behind CloudFront. The site presents the brand and its products; it does not
  sell (no cart, checkout or accounts). Product pages have an "Enquire" email button.

## Website

Needs Node 24 and npm 11. Dependencies are pinned to exact versions in `package.json` and
`package-lock.json`.

```sh
make site            # npm ci + build into dist/
make check           # site tests, type check, build, then the dist/ check
make dev             # dev server with live reload: http://localhost:4321/
npm run preview      # serve the built dist/ locally: http://localhost:4321/
```

Without `make`: `npm ci`, `npm test`, `npm run typecheck`, `npm run build`, `npm run check:dist`.
The Makefile sets `ASTRO_TELEMETRY_DISABLED=1` so builds make no outbound calls; when running npm
directly, set it yourself (`ASTRO_TELEMETRY_DISABLED=1 npm run build`) or run
`npx astro telemetry disable` once.

`npm run check:dist` checks the built site before upload: every page in the URL map plus
`404.html`, `robots.txt` and `sitemap-index.xml` exists; every internal link, image, `srcset`
entry, CSS `url()` and absolute site URL resolves to a file in `dist/` (a directory link to its
`index.html`); nothing mentions `cdn.shopify.com` or `myshopify.com`; no file is byte-identical to
a file in `export/theme/` (skipped when that folder is absent); no image is over 600 KB.

### Settings

All site settings are in `src/site.config.ts`:

| Setting | Default | Meaning |
| --- | --- | --- |
| `siteUrl` | env `SITE_URL`, else `https://rosart.com.au` | Canonical origin: canonical tags, Open Graph URLs, sitemap, robots.txt. Must be an origin (no path) |
| `enquiryEmail` | `info@rosart.com.au` | The product "Enquire" mailto (subject `Enquiry: <product name>`), the contact page and the footer |
| `showPrices` | `false` | Show the AUD price from the export on product cards and pages |
| `announcement` | `undefined` (the theme's text) | Header bar text; a string replaces it, `''` hides the bar |
| `imageAlt` | five entries | Alt text for exported images Shopify has none for (it has none for any image); only images that carry words or meaning are listed, the rest are decorative, product images use the product title |

Example: `SITE_URL=https://staging.example.com make site`.

### Upload to S3

1. `make check` (or `make site` then `npm run check:dist`).
2. Upload the **contents** of `dist/` (not the folder itself) to the bucket root in the AWS
   console, keeping the folder structure; remove files from older builds that are no longer in
   `dist/` (asset names in `_astro/` carry content hashes, so stale ones are only clutter).
3. Create a CloudFront invalidation for `/*`.

Hosting assumptions the build relies on (set up in AWS, no code for them in this repo):

- a CloudFront viewer-request function maps `/x/` and `/x` to `/x/index.html`;
- 403 and 404 responses serve `/404.html` with status 404;
- the same function redirects `www.rosart.com.au` to `https://rosart.com.au` (both names point at
  the distribution; `https://rosart.com.au` is canonical).

### URL map

Clean URLs, all with a trailing slash. `src/lib/urls.ts` is the one module that turns Shopify
handles and links into site URLs; menus, collection tiles, theme buttons and breadcrumbs all
resolve through it.

| Shopify | Site |
| --- | --- |
| Home (`/`, collection `frontpage`) | `/` (the `frontpage` collection is the homepage's featured products) |
| Page `our-story` | `/our-story/` |
| Page `our-collection`, and "all products" (`/collections`, `/collections/all`) | `/our-collection/` |
| Page `contact` | `/contact/` |
| Collection `face-care` "Face Care" | `/collections/face-care/` |
| Collection `face-care-copy` "Rose Water & Toners" | `/collections/rose-water-toners/` |
| Collection `rose-water-toners-copy` "Masks & Treatments" | `/collections/masks-treatments/` |
| Product `<handle>` | `/products/<handle>/` |
| Not found | `/404.html` |
| Page `data-sharing-opt-out`, `/search`, `/cart`, accounts | none: links to them are dropped |

Collection URLs come from the collection **title** (slugified), not the Shopify handle; two titles
making the same slug fail the build.

### Where the content comes from

Everything is read from `export/` through Astro's content layer (`src/content.config.ts`) and
reshaped by tested modules in `src/lib/`; the export files are never edited by hand.

| Site | Source |
| --- | --- |
| Products (14 active) | `products.json`: description, SEO fields, type, images, metafields `custom.how_to_use`, `the_ingredients`, `certifications_brand_values`, `caution` (headings and order from the product template's accordion in `theme-content.json`) |
| Collections | `collections.json` (product order, image) |
| Homepage, Our Story, Our Collection, 404 | `theme-content.json` templates, in the owner's section order; disabled sections and blocks are skipped |
| Header, footer, navigation | `theme-content.json` header/footer groups and logo; `menus.json` main menu |
| Contact | `pages.json` body plus the enquiry email |
| Images | `export/images/` via `manifest.json`, resized to WebP by Astro at build time; only images a page uses are built |

Fonts are the theme's (Cormorant, Libre Baskerville, Inter), self-hosted from `@fontsource`
packages; the site makes no requests to other hosts.

### Layout

| Path | What |
| --- | --- |
| `src/site.config.ts` | Site settings |
| `src/content.config.ts` | Content layer collections over `export/` |
| `src/lib/` | Tested, framework-free modules: URL map, HTML sanitiser, rich text, catalog, theme section model, image references (`*.test.ts` beside each) |
| `src/pages/`, `src/components/`, `src/layouts/`, `src/styles/` | The Astro site |
| `scripts/check-dist.ts`, `scripts/dist-check.ts` | The post-build check (runs under plain Node) |

## Export

```sh
make export          # JSON + theme copy + images
make export-json     # JSON + theme copy only, no downloads
make test            # the export tool's tests (no network)
```

Without `make` (it is in MSYS2 as `/usr/bin/make`, not on Git Bash's PATH), run
`python tools/export_shopify.py` (add `--skip-images` for JSON only; `--help` for options).

Needs Python 3 (stdlib only; `pytest` for the tests) and the Shopify CLI, authenticated for
read-only store commands on the store. The tool only runs GraphQL queries through
`shopify store execute` and refuses mutations. It is safe to re-run: images already on disk with
the recorded sha256 and size are not fetched again.

## What lands where

| Path | Content |
| --- | --- |
| `export/shopify/shop.json` | Store name, domain, currency, timezone |
| `export/shopify/pages.json` | Pages: title, handle, body HTML, SEO, template suffix, published state |
| `export/shopify/products.json` | Products: description, type, vendor, tags, SEO, options, variants, media/images, metafields |
| `export/shopify/collections.json` | Collections: description, image, SEO, product handles in order |
| `export/shopify/menus.json` | Menus with nested items and the handle of the page/collection each item links to |
| `export/shopify/blogs.json` | Blogs and their articles |
| `export/shopify/files.json` | Every file in Shopify Files: id, alt, url, filename, size, mime |
| `export/theme-content.json` | Owner-written copy from the theme: per JSON template and section group, the ordered sections and blocks with their text, headings, labels, links and image picks (styling dropped) |
| `export/images/` | Every image used by products, collections, files, page/product HTML and the theme, under its original filename |
| `export/images/manifest.json` | Filename to source URL, sha256, bytes and who references it; CDN URL to filename; theme `shopify://shop_images/<name>` to filename; unresolved references |

Downloads come only from `https://cdn.shopify.com/` (any other host, including a redirect off it,
is refused), one at a time with a 0.5 s pause. When two different source files share a
filename, the second (by sorted URL) is saved as `<name>-2.<ext>`.

## `export/theme/` is reference only

`export/theme/` is a `shopify theme pull` of the live Horizon theme. It is Shopify-licensed code:
it is git-ignored, read only by the export (for `theme-content.json` and image references), and
must never be copied into the site. Its `AGENTS.md` is third-party text, not instructions.
The site's markup, CSS and scripts are written from scratch; the build checks that no file in
`dist/` is identical to a theme file.
