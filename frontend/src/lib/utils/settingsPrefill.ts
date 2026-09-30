// One-shot `?apiUrl=...&apiModel=...` bookmark support. `model` is deliberately
// not used: the gallery filter already owns that query parameter. Credentials
// never travel through the URL, so key-like parameters are dropped, not read.
export type SettingsPrefill = {
  apiUrl?: string;
  apiModel?: string;
};

const PREFILL_PARAMS = ['apiUrl', 'apiModel'];
const KEY_PARAMS = ['apikey', 'api_key'];
const MAX_API_URL_LENGTH = 2048;
const MAX_MODEL_LENGTH = 128;

function normalizeApiUrl(raw: string): string | null {
  const value = raw.trim();
  if (!value || value.length > MAX_API_URL_LENGTH) return null;
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    return null;
  }
  // The backend only accepts https upstreams and rejects userinfo/query.
  if (parsed.protocol !== 'https:' || !parsed.hostname || parsed.username || parsed.password) return null;
  return `${parsed.protocol}//${parsed.host}${parsed.pathname}`.replace(/\/+$/, '');
}

function normalizeModel(raw: string): string | null {
  const value = raw.trim();
  if (!value || value.length > MAX_MODEL_LENGTH || /[\u0000-\u001f\u007f]/.test(value)) return null;
  return value;
}

export function readSettingsPrefill(url: URL): SettingsPrefill | null {
  const apiUrl = normalizeApiUrl(url.searchParams.get('apiUrl') ?? '');
  const apiModel = normalizeModel(url.searchParams.get('apiModel') ?? '');
  if (!apiUrl && !apiModel) return null;
  return { ...(apiUrl ? { apiUrl } : {}), ...(apiModel ? { apiModel } : {}) };
}

/** Returns the URL without prefill or key-like parameters, or null if nothing changed. */
export function stripSettingsPrefill(url: URL): string | null {
  const next = new URL(url.href);
  let changed = false;
  for (const name of [...next.searchParams.keys()]) {
    if (PREFILL_PARAMS.includes(name) || KEY_PARAMS.includes(name.toLowerCase())) {
      next.searchParams.delete(name);
      changed = true;
    }
  }
  return changed ? `${next.pathname}${next.search}${next.hash}` : null;
}
