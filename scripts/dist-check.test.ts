import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { checkDist, sha256 } from './dist-check.ts';

let dist: string;

function put(path: string, content: string | Buffer): void {
  const full = join(dist, path);
  mkdirSync(dirname(full), { recursive: true });
  writeFileSync(full, content);
}

const page = (body: string) => `<!doctype html><html><head></head><body>${body}</body></html>`;

function goodSite(): void {
  put('index.html', page('<a href="/products/a/">A</a><img src="/_astro/a.webp" srcset="/_astro/a.webp 400w, /_astro/b.webp 800w">'));
  put('products/a/index.html', page('<link rel="canonical" href="https://rosart.com.au/products/a/"><a href="/">home</a>'));
  put('404.html', page('<a href="/">home</a>'));
  put('contact/index.html', page('<a href="mailto:info@rosart.com.au?subject=Hi">info@rosart.com.au</a>'));
  put('_astro/a.webp', Buffer.alloc(1000));
  put('_astro/b.webp', Buffer.alloc(1000));
  put('_astro/site.css', '@font-face{src:url(/_astro/font.woff2)}');
  put('_astro/font.woff2', 'font');
}

const options = (overrides = {}) => ({
  requiredPages: ['/', '/products/a/', '/404.html'],
  siteUrl: 'https://rosart.com.au',
  forbiddenHashes: new Set<string>(),
  maxImageBytes: 600 * 1024,
  allowedEmails: ['info@rosart.com.au'],
  ...overrides,
});

beforeEach(() => {
  dist = mkdtempSync(join(tmpdir(), 'dist-check-'));
});
afterEach(() => {
  rmSync(dist, { recursive: true, force: true });
});

describe('checkDist', () => {
  it('passes a complete site', () => {
    goodSite();
    expect(checkDist(dist, options())).toEqual([]);
  });

  it('reports a missing required page', () => {
    goodSite();
    expect(checkDist(dist, options({ requiredPages: ['/', '/collections/face-care/'] }))).toEqual([
      'missing page /collections/face-care/ (expected collections/face-care/index.html)',
    ]);
  });

  it('reports internal links, srcset entries, absolute site URLs and CSS urls that point nowhere', () => {
    goodSite();
    put(
      'products/a/index.html',
      page(
        '<a href="/nope/">x</a><img src="/_astro/a.webp" srcset="/_astro/gone.webp 2x">' +
          '<meta property="og:image" content="https://rosart.com.au/_astro/missing.jpg">' +
          '<a href="https://example.com/x">ext</a><a href="mailto:x@y.z">m</a><a href="#top">t</a>',
      ),
    );
    put('_astro/site.css', '@font-face{src:url(/_astro/nofont.woff2) format("woff2")}');
    expect(checkDist(dist, options()).sort()).toEqual(
      [
        '_astro/site.css: broken reference /_astro/nofont.woff2',
        'products/a/index.html: broken reference /nope/',
        'products/a/index.html: broken reference /_astro/gone.webp',
        'products/a/index.html: broken reference https://rosart.com.au/_astro/missing.jpg',
      ].sort(),
    );
  });

  it('accepts a directory link without a trailing slash when its index.html exists', () => {
    goodSite();
    put('404.html', page('<a href="/products/a">a</a>'));
    expect(checkDist(dist, options())).toEqual([]);
  });

  it('reports references to the old Shopify store', () => {
    goodSite();
    put('404.html', page('<img src="https://cdn.shopify.com/s/files/x.png">'));
    put('_astro/app.js', 'fetch("https://rosart-mhfmyuxq.myshopify.com/cart")');
    expect(checkDist(dist, options()).sort()).toEqual([
      '404.html: references cdn.shopify.com',
      '_astro/app.js: references myshopify.com',
    ]);
  });

  it('reports a file copied from the Shopify theme', () => {
    goodSite();
    put('_astro/theme.css', 'licensed theme css');
    expect(checkDist(dist, options({ forbiddenHashes: new Set([sha256(Buffer.from('licensed theme css'))]) }))).toEqual([
      '_astro/theme.css: identical to a file in export/theme/',
    ]);
  });

  it('reports images over the size limit', () => {
    goodSite();
    put('_astro/huge.png', Buffer.alloc(700 * 1024));
    put('_astro/big.js', Buffer.alloc(700 * 1024));
    expect(checkDist(dist, options())).toEqual(['_astro/huge.png: image is 700 KB, over the 600 KB limit']);
  });

  it('reports any email address other than the enquiry email, naming the page and the address', () => {
    goodSite();
    put('contact/index.html', page('<p>Write to info@deniva.com.au or <a href="mailto:info@rosart.com.au">us</a></p>'));
    put('products/a/index.html', page('<a href="/">home</a><a href="mailto:sales%40example.com">x</a> hello&#64;example.org'));
    expect(checkDist(dist, options()).sort()).toEqual([
      'contact/index.html (page /contact/): email address info@deniva.com.au is not the site email info@rosart.com.au',
      'products/a/index.html (page /products/a/): email address hello@example.org is not the site email info@rosart.com.au',
      'products/a/index.html (page /products/a/): email address sales@example.com is not the site email info@rosart.com.au',
    ]);
  });

  it('reports an address once per file and checks every text file, not only pages', () => {
    goodSite();
    put('index.html', page('<a href="/products/a/">A</a> x@other.com.au and again X@Other.com.au'));
    put('robots.txt', 'Contact: admin@other.com.au');
    expect(checkDist(dist, options()).sort()).toEqual([
      'index.html (page /): email address x@other.com.au is not the site email info@rosart.com.au',
      'robots.txt: email address admin@other.com.au is not the site email info@rosart.com.au',
    ]);
  });

  it('accepts the enquiry email in any case and ignores CSS at-rules and @2x file names', () => {
    goodSite();
    put('index.html', page('<a href="/products/a/">A</a> INFO@Rosart.com.au <img src="/_astro/rose@2x.webp" alt="">'));
    put('_astro/site.css', '@font-face{src:url(/_astro/font.woff2)}@media (min-width:1px){a{color:red}}');
    put('_astro/rose@2x.webp', Buffer.alloc(10));
    expect(checkDist(dist, options())).toEqual([]);
  });
});
