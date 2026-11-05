/** Finds every image reference (`{ file, alt }`) in the site content, so only those get built. */
function isImageRef(value: unknown): value is { file: string; alt: string } {
  return (
    typeof value === 'object' &&
    value !== null &&
    typeof (value as { file?: unknown }).file === 'string' &&
    typeof (value as { alt?: unknown }).alt === 'string'
  );
}

export function collectImageFiles(...values: unknown[]): string[] {
  const files = new Set<string>();
  const walk = (value: unknown): void => {
    if (Array.isArray(value)) {
      value.forEach(walk);
    } else if (isImageRef(value)) {
      files.add(value.file);
    } else if (typeof value === 'object' && value !== null) {
      Object.values(value).forEach(walk);
    }
  };
  values.forEach(walk);
  return [...files].sort();
}
