import type { APIRoute } from 'astro';
import { getPage, getProducts, getShopCollections, getThemePage } from '../data/queries.ts';
import { buildLlmsTxt, productSummary, wholeSentences } from '../lib/llms.ts';
import { describeSections } from '../lib/theme.ts';
import { siteConfig } from '../site.config.ts';

// `/llms.txt` from the same content as the pages; each summary is the text that page uses for its
// meta description, cut back to whole sentences.
export const GET: APIRoute = async () => {
  const [home, storyPage, story, collectionPage, collectionModel, contact, collections, products] = await Promise.all([
    getThemePage('home'),
    getPage('our-story'),
    getThemePage('our-story'),
    getPage('our-collection'),
    getThemePage('our-collection'),
    getPage('contact'),
    getShopCollections(),
    getProducts(),
  ]);

  const text = buildLlmsTxt({
    siteUrl: siteConfig.siteUrl,
    name: 'Rosart',
    summary: wholeSentences(describeSections(home.sections)),
    pages: [
      { title: storyPage.title, url: storyPage.url, summary: wholeSentences(storyPage.seo.description || describeSections(story.sections)) },
      {
        title: collectionPage.title,
        url: collectionPage.url,
        summary: wholeSentences(
          collectionPage.seo.description ||
            describeSections(collectionModel.sections) ||
            `${collectionPage.title} from Rosart: ${collections.map((c) => c.title).join(', ')}.`,
        ),
      },
      { title: contact.title, url: contact.url, summary: wholeSentences(contact.seo.description) },
    ],
    collections: collections.map((c) => ({ title: c.title, url: c.url, summary: wholeSentences(c.seo.description) })),
    products: products.map((p) => ({ title: p.title, url: p.url, summary: productSummary(p) })),
    retailer: { name: 'ORIZEN', url: 'https://orizen.com.au/' },
    enquiryEmail: siteConfig.enquiryEmail,
  });

  return new Response(text, { headers: { 'Content-Type': 'text/plain; charset=utf-8' } });
};
