import { describe, expect, it, vi } from 'vitest';
import { rawExport } from './export-data.ts';
import { parseThemePages } from './site-content.ts';

// Exact-value tests run on the frozen snapshot, never on the live export (src/test-fixtures/export-snapshot).
vi.mock('./export-data.ts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./export-data.ts')>();
  const { fixtureExport } = await import('../test-fixtures/export-snapshot/load.ts');
  return { ...actual, rawExport: fixtureExport };
});

const page = (id: string) => {
  const found = parseThemePages(rawExport.theme).find((p) => p.id === id);
  if (!found) throw new Error(`no theme page ${id}`);
  return found;
};

describe('parseThemePages: homepage', () => {
  const home = page('home');

  it('renders exactly the enabled sections of templates/index.json, in theme order', () => {
    const template = rawExport.theme.templates['templates/index.json'];
    const enabled = (template?.sections ?? []).filter((s) => !s.disabled).map((s) => s.id);
    expect(home.sections.map((s) => s.id)).toEqual(enabled);
  });

  it('keeps the empty theme section as an empty spacer, never as products', () => {
    const empty = home.sections.find((s) => s.id === 'section_kFXkyX');
    expect(empty).toEqual({ id: 'section_kFXkyX', type: 'section', kind: 'spacer' });
    expect(home.sections.map((s) => s.kind)).not.toContain('featured-products');
  });
});
