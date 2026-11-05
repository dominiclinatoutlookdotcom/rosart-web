import { describe, expect, it } from 'vitest';
import { createImageResolver } from './images.ts';
import { rawExport } from './export-data.ts';

const resolver = createImageResolver(rawExport.manifest, rawExport.files, { 'bg1.png': 'Rose petals' });

describe('createImageResolver', () => {
  it('maps a theme shop_images reference to the exported file', () => {
    expect(resolver.fromShopImage('shopify://shop_images/ESPRIT_HONG_KONG.png').file).toBe('ESPRIT_HONG_KONG.png');
  });

  it('maps a CDN URL with a version query to the exported file', () => {
    const ref = resolver.fromUrl(
      'https://cdn.shopify.com/s/files/1/1075/6656/3409/files/RosartBulgarianRoseWaterSet.png?v=1793791940',
    );
    expect(ref.file).toBe('RosartBulgarianRoseWaterSet.png');
  });

  it('uses the alt text given, then the configured alt, then the fallback', () => {
    expect(resolver.fromShopImage('shopify://shop_images/bg1.png', 'Given').alt).toBe('Given');
    expect(resolver.fromShopImage('shopify://shop_images/bg1.png').alt).toBe('Rose petals');
    expect(resolver.fromShopImage('shopify://shop_images/bg3.png', '', 'Fallback').alt).toBe('Fallback');
    expect(resolver.fromShopImage('shopify://shop_images/bg3.png').alt).toBe('');
  });

  it('refuses references the export does not hold, so a missing image fails the build', () => {
    expect(() => resolver.fromShopImage('shopify://shop_images/missing.png')).toThrow(/missing\.png/);
    expect(() => resolver.fromUrl('https://cdn.shopify.com/s/files/1/x/files/missing.png?v=1')).toThrow(/missing\.png/);
  });
});
