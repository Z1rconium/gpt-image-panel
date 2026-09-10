let cached: boolean | null = null;

/**
 * Feature-detects WebGL once and caches the result - the exposure never
 * attempts a context it can't get, and every caller shares one probe.
 */
export function supportsWebgl(): boolean {
  if (cached !== null) return cached;
  if (typeof document === 'undefined') {
    cached = false;
    return cached;
  }
  try {
    const canvas = document.createElement('canvas');
    const gl = canvas.getContext('webgl2') || canvas.getContext('webgl');
    cached = Boolean(gl);
  } catch {
    cached = false;
  }
  return cached;
}
