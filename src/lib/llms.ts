/**
 * `/llms.txt` (the llmstxt.org proposal): a plain Markdown index of the site for language models,
 * built at build time from the same content as the pages (`src/pages/llms.txt.ts`), never by hand.
 * No major AI search engine has confirmed it reads the file, so it is cheap insurance only.
 */
import { htmlToText } from './html.ts';
import { truncate } from './text.ts';

export interface LlmsLink {
  title: string;
  /** Site-relative URL, such as `/products/x/`; made absolute from `siteUrl`. */
  url: string;
  summary: string;
}

export interface LlmsTxtInput {
  /** Site origin, such as `https://rosart.com.au`. */
  siteUrl: string;
  name: string;
  summary: string;
  pages: readonly LlmsLink[];
  collections: readonly LlmsLink[];
  products: readonly LlmsLink[];
  retailer: { name: string; url: string };
  enquiryEmail: string;
}

const oneLine = (text: string): string => text.replace(/\s+/g, ' ').trim();
const linkText = (text: string): string => oneLine(text).replace(/[\\[\]]/g, '\\$&');

/** Text up to its last complete sentence: drops the unfinished end a truncated description has. */
export function wholeSentences(text: string): string {
  const clean = oneLine(text);
  if (!clean.endsWith('…')) return clean;
  const ends = [...clean.matchAll(/[.!?](?=\s)/g)];
  const last = ends.at(-1);
  return last?.index === undefined ? clean : clean.slice(0, last.index + 1);
}

/** The first sentence of a text, or the whole text when it has no sentence end. */
export function firstSentence(text: string): string {
  const clean = oneLine(text);
  const end = /[.!?](?=\s|$)/.exec(clean);
  return end ? clean.slice(0, end.index + 1) : clean;
}

/**
 * A product's one-line summary: the SEO description written in Shopify, or the first sentence of
 * the description when Shopify has none (the catalog then fills the SEO description with the
 * truncated description text, which is recognised here).
 */
export function productSummary(product: { seo: { description: string }; descriptionHtml: string }): string {
  const text = htmlToText(product.descriptionHtml);
  const seo = oneLine(product.seo.description);
  if (seo && seo !== truncate(text)) return seo;
  return firstSentence(text);
}

function section(heading: string, links: readonly LlmsLink[], siteUrl: string): string[] {
  if (links.length === 0) return [];
  return [
    `## ${heading}`,
    '',
    ...links.map((link) => {
      const summary = oneLine(link.summary);
      const entry = `- [${linkText(link.title)}](${new URL(link.url, siteUrl).href})`;
      return summary ? `${entry}: ${summary}` : entry;
    }),
    '',
  ];
}

export function buildLlmsTxt(input: LlmsTxtInput): string {
  const { siteUrl, retailer } = input;
  return [
    `# ${oneLine(input.name)}`,
    '',
    `> ${oneLine(input.summary)}`,
    '',
    ...section('Pages', input.pages, siteUrl),
    ...section('Collections', input.collections, siteUrl),
    ...section('Products', input.products, siteUrl),
    '## Where to buy',
    '',
    `Buy ${oneLine(input.name)} products from [${linkText(retailer.name)}](${retailer.url}), the Australian retail partner. Enquiries: ${input.enquiryEmail}`,
    '',
  ].join('\n');
}
