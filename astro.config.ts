import sitemap from '@astrojs/sitemap';
import { defineConfig } from 'astro/config';
import { siteConfig } from './src/site.config.ts';

// Plain files for a private S3 bucket behind CloudFront: a viewer-request function maps `/x/`
// and `/x` to `/x/index.html`, and 403/404 serve `/404.html`. So: static output, one
// index.html per directory, trailing slashes everywhere, and no server features.
export default defineConfig({
  site: siteConfig.siteUrl,
  output: 'static',
  trailingSlash: 'always',
  build: {
    format: 'directory',
    assets: '_astro',
  },
  integrations: [
    sitemap({
      filter: (page) => !/\/404\/?$/.test(new URL(page).pathname),
    }),
  ],
  prefetch: false,
  devToolbar: { enabled: false },
});
