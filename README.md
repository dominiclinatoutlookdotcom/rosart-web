# rosart-web

Rosart is an organic Bulgarian rose skincare brand. Its public site is a static Astro build on S3
behind CloudFront; its Shopify store (`rosart-mhfmyuxq.myshopify.com`) stays the content editor
(owner decision 2026-11-07), and `make publish` turns the store's current content into the site.

This repo holds two things:

- a **read-only content export** of that store (`export/`): the store's content as JSON plus
  every image it uses, refreshed from the store on every publish; it is also the repo's own copy
  of the content and images;
- the **static website** (Astro, `src/`), built from that export into plain files in `dist/` for a
  private S3 bucket behind CloudFront. The site presents the brand and its products; it does not
  sell (no cart, checkout or accounts). Product pages have an "Enquire" email button and a
  "Shop in Orizen.com.au" button to ORIZEN, the Australian retail partner.

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
`404.html`, `robots.txt`, `llms.txt` and `sitemap-index.xml` exists; every internal link, image,
`srcset` entry, CSS `url()` and absolute site URL resolves to a file in `dist/` (a directory link to
its `index.html`); every link in `llms.txt` to this site is an absolute site URL of a page; nothing mentions `cdn.shopify.com` or `myshopify.com`; no file is byte-identical to
a file in `export/theme/` (skipped when that folder is absent); no image is over 600 KB; and no
email address other than `enquiryEmail` appears in any text file (page text, `mailto:` links,
`%40` and `&#64;` forms included). The site has one email address: an address in Shopify content
is fixed in Shopify and re-exported, never rewritten by the site, so until then the check fails
and names the page and the address (for example
`contact/index.html (page /contact/): email address info@deniva.com.au is not the site email info@rosart.com.au`).

`/llms.txt` ([llmstxt.org](https://llmstxt.org) proposal) is a plain Markdown index of the site for
language models: a summary, then links to the pages, collections and products with one-line
summaries, and where to buy. It is generated at build time (`src/pages/llms.txt.ts`, builder
`src/lib/llms.ts`) from the same content as the pages, so it is never edited by hand. Expect little
from it: as of 2026-11-07 no major AI search engine has confirmed it reads the file.

### Settings

All site settings are in `src/site.config.ts`:

| Setting | Default | Meaning |
| --- | --- | --- |
| `siteUrl` | env `SITE_URL`, else `https://rosart.com.au` | Canonical origin: canonical tags, Open Graph URLs, sitemap, robots.txt. Must be an origin (no path) |
| `enquiryEmail` | `info@rosart.com.au` | The product "Enquire" mailto (subject `Enquiry: <product name>`), the contact page and the footer |
| `orizenButtonLabel` | `Shop in Orizen.com.au` | Label of the product button to ORIZEN (see "ORIZEN button" below) |
| `showPrices` | `false` | Show the AUD price from the export on product cards and pages |
| `announcement` | `undefined` (the theme's text) | Header bar text; a string replaces it, `''` hides the bar |
| `imageAlt` | five entries | Alt text for exported images Shopify has none for (it has none for any image); only images that carry words or meaning are listed, the rest are decorative, product images use the product title |

Example: `SITE_URL=https://staging.example.com make site`.

## Publish

Shopify stays the content editor (owner decision 2026-11-07): products, pages, collections and
menus are edited in the Shopify admin, and the homepage, Our Story, header and footer text in the
Shopify **theme editor**. Publishing turns the current store content into the live site:

```sh
make publish          # export -> make check -> dry run -> confirm -> sync to S3 -> invalidate CloudFront
make publish-only     # skip the export: make check, then publish the current export/
make publish-dryrun   # export -> make check -> dry run; never uploads or invalidates
make publish PUBLISH_FLAGS="--yes --wait"   # no prompt; wait for the invalidation to finish
```

Each target first checks `deploy.config.json` and stops at once, calling nothing, while it still
holds placeholders. Then:

1. **Export** (`make export`): `shopify theme pull` refreshes `export/theme/`, which is where the
   theme editor's text comes from (`theme-content.json` is read from it), then the store content
   and images are exported. Both are read-only on the store.
2. **Check** (`make check`): site tests, type check, build into `dist/`, and the dist check.
3. **Publish** (`python tools/publish.py`):
   - refuses a missing or incomplete `dist/` (no `index.html`, `404.html`, `robots.txt` or
     `sitemap-index.xml`, no `_astro/` assets, or fewer than 10 pages), because the sync deletes
     whatever the bucket holds that `dist/` lacks;
   - runs `aws sts get-caller-identity` and prints the account and ARN;
   - dry-runs every sync (`--dryrun`) and prints how many files would be uploaded and deleted,
     and every key it would delete;
   - asks `Type yes to continue` (only `yes` proceeds; `--yes` skips the prompt; with no terminal
     and no `--yes` it refuses);
   - syncs, then creates a CloudFront invalidation for `/*` and prints its ID and the command to
     check it (`--wait` waits for it instead). A failed sync stops the run before the invalidation.

Every `aws` command passes `--profile` and `--region` from the config explicitly, and the script
refuses (in code, before running it) any command that is not one of these operations on the
configured bucket and distribution. It never reads credential files: it strips `AWS_ACCESS_KEY_ID`,
`AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, `AWS_PROFILE` and `AWS_DEFAULT_PROFILE` from the
CLI's environment, so the named profile is the only credential source.

The sync runs in six steps so caching and content types are right and no page ever points at a
missing asset:

| Step | Source to target | Flags | Cache-Control |
| --- | --- | --- | --- |
| 1 | `dist/_astro` to `s3://<bucket>/_astro` | upload only | `public, max-age=31536000, immutable` |
| 2 | `dist` to `s3://<bucket>` | `--delete --exclude "*" --include "*.html" --exclude "_astro/*"`, `--content-type "text/html; charset=utf-8"` | `public, max-age=0, must-revalidate` |
| 3 | `dist` to `s3://<bucket>` | `--delete --exclude "*" --include "*.txt" --exclude "_astro/*"`, `--content-type "text/plain; charset=utf-8"` | `public, max-age=0, must-revalidate` |
| 4 | `dist` to `s3://<bucket>` | `--delete --exclude "*" --include "*.xml" --exclude "_astro/*"`, `--content-type "application/xml; charset=utf-8"` | `public, max-age=0, must-revalidate` |
| 5 | `dist` to `s3://<bucket>` | `--delete --exclude "*.html" --exclude "*.txt" --exclude "*.xml" --exclude "_astro/*"` | `public, max-age=0, must-revalidate` |
| 6 | `dist/_astro` to `s3://<bucket>/_astro` | `--delete` (prunes old hashed assets) | `public, max-age=31536000, immutable` |

The root steps end their filters with `--exclude "_astro/*"` (the last matching filter wins, and
`aws s3 sync --delete` never deletes excluded keys), so they cannot touch `_astro/`; the asset
steps sync that folder to its own prefix, so their `--delete` cannot reach anything else.

Steps 2 to 4 set the type explicitly so HTML, `robots.txt`, `llms.txt` (which holds non-ASCII
text) and the sitemaps are served as UTF-8. Each of them owns its pattern: it uploads and deletes
only those files, and step 5 excludes them. Step 5 must not include them, because `aws s3 sync`
uploads any file newer than its object, so with the local clock ahead of S3 it would upload them
again with the CLI's guessed type (no charset). Together the root steps still mirror `dist/`. Other
content types (CSS, WebP, fonts, JSON) are inferred by the AWS CLI.

### Deploy config

`deploy.config.json` (committed; no credentials, ever):

| Key | Meaning |
| --- | --- |
| `profile` | AWS CLI profile, default `rosart-deploy` |
| `bucket` | S3 bucket name (not a URL) |
| `region` | The bucket's region, e.g. `ap-southeast-2` |
| `distributionId` | CloudFront distribution ID, e.g. `E2ABCDEF123456` |

`REPLACE_ME` values, unknown keys and malformed values are refused.
`python tools/publish.py --check-config` validates it without calling anything.

### AWS prerequisites (one-time, done by the owner)

1. **Bucket**: a private S3 bucket (Block Public Access on, Object Ownership "bucket owner
   enforced", default SSE-S3 encryption: an SSE-KMS key would need KMS permissions too).
2. **CloudFront**: a distribution with the bucket as origin through **Origin Access Control**
   (OAC), plus the hosting rules below. The bucket policy allows only that distribution to read:

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [{
       "Sid": "CloudFrontReadOnly",
       "Effect": "Allow",
       "Principal": { "Service": "cloudfront.amazonaws.com" },
       "Action": "s3:GetObject",
       "Resource": "arn:aws:s3:::<BUCKET>/*",
       "Condition": { "StringEquals": { "AWS:SourceArn": "arn:aws:cloudfront::<ACCOUNT_ID>:distribution/<DISTRIBUTION_ID>" } }
     }]
   }
   ```

   Without `s3:ListBucket` for CloudFront, a missing key answers 403, which the 403/404 rule
   below turns into `/404.html`.
3. **Deploy user**: an IAM user (for example `rosart-deploy`) with only this policy:

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Sid": "ListSiteBucket",
         "Effect": "Allow",
         "Action": "s3:ListBucket",
         "Resource": "arn:aws:s3:::<BUCKET>"
       },
       {
         "Sid": "WriteSiteObjects",
         "Effect": "Allow",
         "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
         "Resource": "arn:aws:s3:::<BUCKET>/*"
       },
       {
         "Sid": "InvalidateSiteCache",
         "Effect": "Allow",
         "Action": ["cloudfront:CreateInvalidation", "cloudfront:GetInvalidation", "cloudfront:ListInvalidations"],
         "Resource": "arn:aws:cloudfront::<ACCOUNT_ID>:distribution/<DISTRIBUTION_ID>"
       }
     ]
   }
   ```

   `sts:GetCallerIdentity` needs no permission.
4. **Profile**: create an access key for that user and store it in the AWS CLI (AWS CLI v2 must be
   on PATH), in zsh:

   ```zsh
   aws configure --profile rosart-deploy
   # AWS Access Key ID / Secret Access Key: the deploy user's key
   # Default region name: the bucket's region, e.g. ap-southeast-2
   # Default output format: json
   aws sts get-caller-identity --profile rosart-deploy   # shows the deploy user's ARN
   ```

   The CLI keeps the key in its own files under your user profile; it never goes in this repo.
5. Fill in `bucket`, `region` and `distributionId` in `deploy.config.json`, then run
   `make publish-dryrun` and read the plan before the first `make publish`.

### Hosting rules

Hosting assumptions the build relies on (set up in AWS, no code for them in this repo):

- a CloudFront viewer-request function maps `/x/` and `/x` to `/x/index.html`;
- 403 and 404 responses serve `/404.html` with status 404;
- the same function redirects `www.rosart.com.au` to `https://rosart.com.au` (both names point at
  the distribution; `https://rosart.com.au` is canonical).

### ORIZEN button

Each product page has, beside "Enquire", a button labelled `orizenButtonLabel` that opens ORIZEN
in a new tab (`target="_blank" rel="noopener noreferrer"`), with no tracking parameters added:

- the product's **`custom.orizen_url`** metafield (a URL metafield defined in Shopify), used only
  when it is `https://` on `orizen.com.au` or `www.orizen.com.au` (no other host, port or
  user info);
- otherwise, including while the metafield is unset, `https://orizen.com.au/search?q=<product
  title, URL-encoded>`.

Under the buttons is the theme's purchase note, taken from the product template (the text block
after its "SHOP NOW" button), so the owner edits it in the theme editor. Rules and tests:
`src/lib/commerce.ts` (`orizenUrl`), `src/lib/theme.ts` (`productPurchaseNote`).

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
| Products (14 active) | `products.json`: description, SEO fields, type, images, metafields `custom.how_to_use`, `the_ingredients`, `certifications_brand_values`, `caution` (headings and order from the product template's accordion in `theme-content.json`), and `custom.orizen_url` for the ORIZEN button |
| Product purchase note | `theme-content.json`, product template: the text block after its button ("You’ll be redirected to ORIZEN, …") |
| 404 page, including its button label ("Continue shopping") | `theme-content.json` 404 template, edited in the theme editor |
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
| `tools/publish.py`, `deploy.config.json` | The publish script and its deploy target |

## Export

```sh
make theme-pull      # refresh export/theme/ from the live theme only
make export          # theme pull, then JSON + theme copy + images
make export-json     # theme pull, then JSON + theme copy only, no downloads
make test            # the Python tests: export, theme pull, publish (no network, no AWS)
```

Both export targets first run `python tools/pull_theme.py`: `shopify theme pull --store
rosart-mhfmyuxq.myshopify.com --theme 201570156625 --path export/theme`, from a neutral working
folder, read-only on the store. That is how text changed in the theme editor reaches the site.

Without `make` (it is in MSYS2 as `/usr/bin/make`, not on Git Bash's PATH), run
`python tools/pull_theme.py`, then `python tools/export_shopify.py` (add `--skip-images` for JSON
only; `--help` for options). The exporter alone reads whatever `export/theme/` holds.

Needs Python 3 (stdlib only; `pytest` for the tests) and the Shopify CLI, authenticated for
read-only store commands on the store. The tool only runs GraphQL queries through
`shopify store execute` and refuses mutations. It is safe to re-run: images already on disk with
the recorded sha256 and size are not fetched again.

## SEO plan

The SEO and AI-search fixes for the Shopify store go through one owner-approved file, following
the listing checklist's "Writing in bulk" rules (dry run, approval by id, batches, rollback log,
read-back).

```sh
python tools/seo_plan.py                 # 1. plan: writes seo/plan.json and seo/plan.md (local files only)
python tools/seo_check.py seo/plan.json  # 2. check: limits, uniqueness, facts against sources
sha256sum seo/plan.json                  # 3. approve: the owner reviews seo/plan.md and approves this hash
make seo-apply-dryrun                    # 4. dry run: changes per field, scope and drift checks; writes nothing
make seo-apply APPROVED=<sha256>         # 5. apply: batches of 10, rollback log, read-back
python tools/seo_apply.py --rollback seo/apply-log-<UTC>.jsonl          # 6. rollback dry run
python tools/seo_apply.py --rollback seo/apply-log-<UTC>.jsonl --write  #    rollback
```

- **Approve by hash.** `seo_apply.py` refuses unless the file's sha256 equals `--approved` exactly,
  the plan names `rosart-mhfmyuxq.myshopify.com`, and `seo_check` passes. Regenerating or editing
  the plan changes the hash and needs a new approval. The dry run prints the hash too.
- **Scopes.** It reads `currentAppInstallation { accessScopes }` first. A write run refuses when
  the CLI login lacks a scope the plan needs (`write_products`, `write_files`, `write_content`)
  and names the missing ones. Re-run `shopify store auth` with those scopes.
- **Drift.** Each batch re-reads its targets before writing. A field whose live value is not the
  plan's `before` (someone edited it since) is skipped and reported, never overwritten. A field
  already at `after` is skipped as done, so a stopped run can be re-run as is.
- **Writes.** It uses `productUpdate`, `collectionUpdate`, `pageUpdate` (the `global.title_tag` and
  `description_tag` metafields), and one `fileUpdate` per batch for alt text, with 2 s between
  batches. Homepage (`manual`) entries are only listed: set them in the admin. A null payload or
  `userErrors` counts as a failure, because Shopify can refuse silently.
- **Rollback log.** Before each write, the exact live value is appended and synced to
  `seo/apply-log-<UTC>.jsonl`. `--rollback` restores those values, with the same drift check
  against the value the apply wrote. It writes its own log as well.
- **Read-back.** After each batch it re-reads every written field and compares the whole value
  with `after`. The run stops after the first batch with a failure or mismatch. Exit status 0
  means clean; 1 means refused, drift, a failure or a mismatch (see the report).
- **Encoding (Windows).** The CLI is never read through a stdout pipe. Variables go in an
  ASCII-escaped JSON file, results come from `--output-file` decoded as strict UTF-8, and the CLI
  runs from a temporary folder (`ShopifyCliFileRunner` in `tools/shopify_cli.py`).
  `--allow-mutations` is passed only on a `--write` run, and only with a mutation.

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
