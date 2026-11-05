/**
 * Image metadata for the exported images a page uses, from the `images` content collection. Only
 * those images are imported, so only their optimised versions land in `dist/`.
 */
import type { ImageMetadata } from 'astro';
import { getEntry } from 'astro:content';

export async function imageFor(file: string): Promise<ImageMetadata> {
  const entry = await getEntry('images', file);
  if (!entry) throw new Error(`export/images/${file} is not in the images collection (no page uses it?)`);
  return entry.data.src;
}

/** Widths to generate for an image: the requested ones, capped at the source width. */
export function widthsFor(meta: ImageMetadata, widths: readonly number[]): number[] {
  return [...new Set(widths.map((w) => Math.min(w, meta.width)))].sort((a, b) => a - b);
}
