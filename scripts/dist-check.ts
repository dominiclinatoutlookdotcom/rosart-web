/**
 * Checks a built `dist/` before it is uploaded to S3: every expected page exists, every internal
 * reference resolves to a file (a directory link to its index.html, as the CloudFront function
 * maps it), nothing points at the old Shopify store, no Shopify theme file was copied, no image
 * is over the size limit, no email address other than the site's enquiry email appears, and every
 * llms.txt link is an absolute site URL of a page.
 * Runs under plain Node (type stripping), so it imports only `.ts` files with no Vite-only features.
 */
import { createHash } from 'node:crypto';
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { extname, join, posix, relative, sep } from 'node:path';

export interface DistCheckOptions {
  /** Site paths that must exist, such as `/products/x/` or `/404.html`. */
  requiredPages: readonly string[];
  /** The site origin; absolute links to it are checked like root-relative ones. */
  siteUrl: string;
  /** sha256 of every file in `export/theme/`: none may appear in dist. */
  forbiddenHashes: ReadonlySet<string>;
  maxImageBytes: number;
  /** The only email addresses the site may show (the enquiry email); any other is reported. */
  allowedEmails: readonly string[];
}

const TEXT_EXTENSIONS = new Set(['.html', '.css', '.js', '.mjs', '.xml', '.txt', '.json', '.svg', '.webmanifest']);
const IMAGE_EXTENSIONS = new Set(['.png', '.jpg', '.jpeg', '.webp', '.avif', '.gif', '.svg']);
const FORBIDDEN_HOSTS = ['cdn.shopify.com', 'myshopify.com'];
const EMAIL = /[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}/g;
/** `name@2x.webp` and similar file names look like addresses; their "domain" is a file name. */
const FILE_NAME_DOMAIN = /\.(?:png|jpe?g|webp|avif|gif|svg|css|js|mjs|woff2?|ico|html|xml|txt|json)$/i;

export function sha256(content: Buffer): string {
  return createHash('sha256').update(content).digest('hex');
}

export function listFiles(dir: string): string[] {
  const out: string[] = [];
  const walk = (current: string) => {
    for (const entry of readdirSync(current, { withFileTypes: true })) {
      const full = join(current, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (entry.isFile()) out.push(full);
    }
  };
  if (existsSync(dir)) walk(dir);
  return out;
}

function toPosix(path: string): string {
  return path.split(sep).join('/');
}

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/** The dist file a site path is served from, or undefined when nothing serves it. */
function servedFile(dist: string, sitePath: string): string | undefined {
  const clean = decodeURIComponent(sitePath.split(/[?#]/)[0] ?? '');
  const relativePath = clean.replace(/^\/+/, '');
  const candidates = clean.endsWith('/')
    ? [posix.join(relativePath, 'index.html')]
    : [relativePath, posix.join(relativePath, 'index.html')];
  return candidates.find((candidate) => {
    const full = join(dist, candidate);
    return existsSync(full) && statSync(full).isFile();
  });
}

function decodeAttribute(value: string): string {
  return value.replace(/&amp;/g, '&').replace(/&#x2F;/gi, '/').replace(/&quot;/g, '"');
}

/** Internal references in a text file, as written (root-relative paths or absolute site URLs). */
function references(text: string, extension: string, siteUrl: string): string[] {
  const refs = new Set<string>();
  const add = (value: string) => {
    const ref = decodeAttribute(value.trim());
    if (ref.startsWith('/') && !ref.startsWith('//')) refs.add(ref);
    else if (ref === siteUrl || ref.startsWith(`${siteUrl}/`)) refs.add(ref);
  };

  if (extension === '.html') {
    for (const match of text.matchAll(/\s(?:href|src|poster|content)="([^"]*)"/g)) add(match[1] ?? '');
    for (const match of text.matchAll(/\ssrcset="([^"]*)"/g)) {
      for (const candidate of (match[1] ?? '').split(',')) add(candidate.trim().split(/\s+/)[0] ?? '');
    }
  }
  if (extension === '.html' || extension === '.css') {
    for (const match of text.matchAll(/url\(\s*['"]?([^'")\s]+)['"]?\s*\)/g)) add(match[1] ?? '');
  }
  const absolute = new RegExp(`${escapeRegExp(siteUrl)}(?:/[^\\s"'<>)\\\\]*)?`, 'g');
  for (const match of text.matchAll(absolute)) add(match[0]);
  return [...refs];
}

/** Email addresses in a text file, lower-cased and de-duplicated, including `%40` and `&#64;` forms. */
function emailAddresses(text: string): string[] {
  const decoded = text.replace(/&#0*64;|&#x0*40;|&commat;|%40/gi, '@');
  const found = new Set<string>();
  for (const match of decoded.matchAll(EMAIL)) {
    const address = match[0].toLowerCase();
    if (!FILE_NAME_DOMAIN.test(address)) found.add(address);
  }
  return [...found];
}

/**
 * `llms.txt` links must be absolute site URLs of pages (an HTML file in dist), so a model that
 * reads it lands on a real page. Links to other hosts (the retailer) and `mailto:` are not checked.
 */
function llmsLinkProblems(dist: string, text: string, siteUrl: string): string[] {
  const problems: string[] = [];
  for (const match of text.matchAll(/\]\(([^)\s]+)\)/g)) {
    const link = match[1] ?? '';
    const isSiteUrl = link === siteUrl || link.startsWith(`${siteUrl}/`);
    if (!isSiteUrl && /^[a-z][a-z0-9+.-]*:/i.test(link)) continue;
    if (!isSiteUrl) {
      problems.push(`llms.txt: link ${link} is not an absolute ${siteUrl} URL`);
      continue;
    }
    const file = servedFile(dist, link.slice(siteUrl.length) || '/');
    if (!file) problems.push(`llms.txt: link ${link} does not resolve to a page in dist`);
    else if (!file.endsWith('.html')) problems.push(`llms.txt: link ${link} is not a page`);
  }
  return problems;
}

/** `contact/index.html (page /contact/)` for a page, the plain file name otherwise. */
function describeFile(name: string): string {
  if (name === 'index.html') return `${name} (page /)`;
  if (name.endsWith('/index.html')) return `${name} (page /${name.slice(0, -'index.html'.length)})`;
  if (name.endsWith('.html')) return `${name} (page /${name})`;
  return name;
}

export function checkDist(dist: string, options: DistCheckOptions): string[] {
  const problems: string[] = [];

  for (const page of options.requiredPages) {
    if (!servedFile(dist, page)) {
      const expected = page.endsWith('/') ? posix.join(page.replace(/^\/+/, ''), 'index.html') : page.replace(/^\/+/, '');
      problems.push(`missing page ${page} (expected ${expected})`);
    }
  }

  for (const full of listFiles(dist)) {
    const name = toPosix(relative(dist, full));
    const extension = extname(full).toLowerCase();
    const content = readFileSync(full);

    if (options.forbiddenHashes.has(sha256(content))) problems.push(`${name}: identical to a file in export/theme/`);

    if (IMAGE_EXTENSIONS.has(extension) && content.length > options.maxImageBytes) {
      const kb = (bytes: number) => Math.round(bytes / 1024);
      problems.push(`${name}: image is ${kb(content.length)} KB, over the ${kb(options.maxImageBytes)} KB limit`);
    }

    if (!TEXT_EXTENSIONS.has(extension)) continue;
    const text = content.toString('utf8');
    for (const host of FORBIDDEN_HOSTS) {
      if (text.includes(host)) problems.push(`${name}: references ${host}`);
    }
    const allowed = new Set(options.allowedEmails.map((email) => email.toLowerCase()));
    for (const address of emailAddresses(text)) {
      if (!allowed.has(address)) {
        problems.push(`${describeFile(name)}: email address ${address} is not the site email ${options.allowedEmails.join(', ')}`);
      }
    }
    if (name === 'llms.txt') {
      problems.push(...llmsLinkProblems(dist, text, options.siteUrl));
      continue;
    }
    for (const ref of references(text, extension, options.siteUrl)) {
      const sitePath = ref.startsWith(options.siteUrl) ? ref.slice(options.siteUrl.length) || '/' : ref;
      if (!servedFile(dist, sitePath)) problems.push(`${name}: broken reference ${ref}`);
    }
  }
  return problems;
}
