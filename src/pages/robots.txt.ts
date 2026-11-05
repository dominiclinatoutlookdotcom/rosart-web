import type { APIRoute } from 'astro';
import { siteConfig } from '../site.config.ts';

export const GET: APIRoute = () =>
  new Response(`User-agent: *\nAllow: /\n\nSitemap: ${new URL('/sitemap-index.xml', siteConfig.siteUrl).href}\n`, {
    headers: { 'Content-Type': 'text/plain; charset=utf-8' },
  });
