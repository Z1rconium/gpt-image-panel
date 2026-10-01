import { describe, expect, it } from 'vitest';
import {
  MAX_PRESET_SHARE_BYTES,
  PRESET_SHARE_PARAM,
  buildPresetShareUrl,
  decodePresetShare,
  encodePresetShare,
  readPresetShare,
  stripPresetShare
} from '$lib/utils/presetShare';

const samplePackage = {
  format: 'gpt-image-panel-presets',
  format_version: 1,
  presets: [{ name: 'Gateway', api_url: 'https://api.example.com', provider_kind: 'openai' }]
};

describe('preset share links', () => {
  it('round-trips a package through the URL-safe encoding', () => {
    const encoded = encodePresetShare(samplePackage);
    expect(encoded).toBeTruthy();
    expect(encoded).not.toMatch(/[+/=]/);
    expect(decodePresetShare(encoded)).toEqual(samplePackage);
  });

  it('rejects oversized packages and invalid payloads', () => {
    expect(encodePresetShare({ data: 'x'.repeat(MAX_PRESET_SHARE_BYTES + 10) })).toBeNull();
    expect(decodePresetShare('%%%not-base64%%%')).toBeNull();
    expect(decodePresetShare(null)).toBeNull();
    expect(decodePresetShare(btoa(JSON.stringify([1, 2, 3])))).toBeNull();
    expect(decodePresetShare(btoa('not json'))).toBeNull();
  });

  it('reads and strips only the preset parameter', () => {
    const encoded = encodePresetShare(samplePackage) as string;
    const url = new URL(`https://panel.example/?${PRESET_SHARE_PARAM}=${encoded}&apiUrl=https%3A%2F%2Fapi.example.com&mode=studio`);
    expect(readPresetShare(url)).toEqual(samplePackage);
    expect(stripPresetShare(url)).toBe(`/?apiUrl=https%3A%2F%2Fapi.example.com&mode=studio`);
    expect(stripPresetShare(new URL('https://panel.example/?mode=studio'))).toBeNull();
  });

  it('builds a share URL or reports that the package does not fit', () => {
    const link = buildPresetShareUrl(samplePackage, 'https://panel.example/');
    expect(link).toBeTruthy();
    const parsed = new URL(link as string);
    expect(readPresetShare(parsed)).toEqual(samplePackage);
    expect(buildPresetShareUrl({ data: 'x'.repeat(MAX_PRESET_SHARE_BYTES + 10) }, 'https://panel.example/')).toBeNull();
  });
});
