// One-shot `?preset=<base64url>` share links for a secret-free preset package.
// The payload is validated by the backend import preview; the link is only a
// transport and never carries credentials.
export const PRESET_SHARE_PARAM = 'preset';
export const MAX_PRESET_SHARE_BYTES = 24 * 1024;

function bytesToBinary(bytes: Uint8Array): string {
  let binary = '';
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
  }
  return binary;
}

function toBase64Url(bytes: Uint8Array): string {
  return btoa(bytesToBinary(bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function fromBase64Url(value: string): Uint8Array | null {
  const normalized = value.replace(/-/g, '+').replace(/_/g, '/');
  const padded = normalized + '='.repeat((4 - (normalized.length % 4)) % 4);
  try {
    const binary = atob(padded);
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) {
      bytes[index] = binary.charCodeAt(index);
    }
    return bytes;
  } catch {
    return null;
  }
}

export function encodePresetShare(packageValue: unknown): string | null {
  let json: string;
  try {
    json = JSON.stringify(packageValue);
  } catch {
    return null;
  }
  const bytes = new TextEncoder().encode(json);
  if (!bytes.length || bytes.length > MAX_PRESET_SHARE_BYTES) return null;
  return toBase64Url(bytes);
}

export function decodePresetShare(value: string | null | undefined): Record<string, unknown> | null {
  const raw = String(value ?? '').trim();
  if (!raw || raw.length > MAX_PRESET_SHARE_BYTES * 2) return null;
  const bytes = fromBase64Url(raw);
  if (!bytes || !bytes.length || bytes.length > MAX_PRESET_SHARE_BYTES) return null;
  try {
    const parsed = JSON.parse(new TextDecoder().decode(bytes));
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null;
    return parsed as Record<string, unknown>;
  } catch {
    return null;
  }
}

export function readPresetShare(url: URL): Record<string, unknown> | null {
  return decodePresetShare(url.searchParams.get(PRESET_SHARE_PARAM));
}

/** Returns the URL without the share parameter, or null if nothing changed. */
export function stripPresetShare(url: URL): string | null {
  if (!url.searchParams.has(PRESET_SHARE_PARAM)) return null;
  const next = new URL(url.href);
  next.searchParams.delete(PRESET_SHARE_PARAM);
  return `${next.pathname}${next.search}${next.hash}`;
}

export function buildPresetShareUrl(packageValue: unknown, href: string): string | null {
  const encoded = encodePresetShare(packageValue);
  if (!encoded) return null;
  const url = new URL(href);
  url.searchParams.set(PRESET_SHARE_PARAM, encoded);
  return url.toString();
}
