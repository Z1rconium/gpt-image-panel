let cached: boolean | null = null;

/**
 * Feature-detects WebGL once and caches the result - the exposure never
 * attempts a context it can't get, and every caller shares one probe. The
 * probe context is explicitly released so detection does not leave a GPU
 * context alive for the lifetime of the tab.
 */
export function supportsWebgl(): boolean {
  if (cached !== null) return cached;
  if (typeof document === 'undefined') {
    cached = false;
    return cached;
  }
  let probe: HTMLCanvasElement | null = null;
  try {
    probe = document.createElement('canvas');
    const gl = probe.getContext('webgl2') || probe.getContext('webgl');
    cached = Boolean(gl);
    gl?.getExtension('WEBGL_lose_context')?.loseContext();
  } catch {
    cached = false;
  } finally {
    probe = null;
  }
  return cached;
}
