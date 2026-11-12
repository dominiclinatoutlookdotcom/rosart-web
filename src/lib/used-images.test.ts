import { describe, expect, it, vi } from 'vitest';
import { collectImageFiles } from './used-images.ts';
import { usedImageFiles } from './site-content.ts';

// Exact-value tests run on the frozen snapshot, never on the live export (tests/fixtures/export-snapshot).
vi.mock('./export-data.ts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./export-data.ts')>();
  const { fixtureExport } = await import('../../tests/fixtures/export-snapshot/load.ts');
  return { ...actual, rawExport: fixtureExport };
});

describe('collectImageFiles', () => {
  it('finds image references anywhere in nested content, once each, sorted', () => {
    const content = {
      logo: { file: 'b.png', alt: 'Logo' },
      sections: [{ blocks: [{ kind: 'image', image: { file: 'a.png', alt: '' } }, { kind: 'group', blocks: [{ image: { file: 'b.png', alt: '' } }] }] }],
      other: { file: 'not-an-image-ref' },
    };
    expect(collectImageFiles(content)).toEqual(['a.png', 'b.png']);
  });
});

describe('usedImageFiles', () => {
  const used = usedImageFiles();

  it('includes the logo, product, collection, theme and connection images', () => {
    for (const file of [
      'ESPRIT_HONG_KONG.png',
      'RosartBulgarianRoseWaterSet.png',
      '1-2.png',
      'bg3.png',
      'Untitled_design_7_f6c26f2b-ad40-440e-a57a-fb6da4fe0dcb.png',
      'ARTEC.png',
    ]) {
      expect(used).toContain(file);
    }
  });

  it('leaves out exported images no page uses', () => {
    expect(used).not.toContain('hero_banner.png');
    expect(used).not.toContain('Aged_Iron_Hatch_with_Rose_Petals.png');
  });
});
