import { describe, expect, it } from 'vitest';
import { readSettingsPrefill, stripSettingsPrefill } from '$lib/utils/settingsPrefill';

const at = (search: string) => new URL(`https://panel.example/${search}`);

describe('readSettingsPrefill', () => {
  it('reads a valid https url and model', () => {
    expect(readSettingsPrefill(at('?apiUrl=https://api.example.com/v1&apiModel=gpt-image-1'))).toEqual({
      apiUrl: 'https://api.example.com/v1',
      apiModel: 'gpt-image-1'
    });
  });

  it('returns null without any usable parameter', () => {
    expect(readSettingsPrefill(at(''))).toBeNull();
    expect(readSettingsPrefill(at('?apiUrl=&apiModel=%20'))).toBeNull();
  });

  it('rejects non-https and credential-bearing urls', () => {
    expect(readSettingsPrefill(at('?apiUrl=http://api.example.com'))).toBeNull();
    expect(readSettingsPrefill(at('?apiUrl=javascript:alert(1)'))).toBeNull();
    expect(readSettingsPrefill(at('?apiUrl=https://user:pass@api.example.com'))).toBeNull();
    expect(readSettingsPrefill(at('?apiUrl=not-a-url'))).toBeNull();
  });

  it('drops query and hash from the url and trailing slashes', () => {
    expect(readSettingsPrefill(at('?apiUrl=https://api.example.com/v1/'))?.apiUrl).toBe('https://api.example.com/v1');
    expect(readSettingsPrefill(at('?apiUrl=https://api.example.com/?key=secret#frag'))?.apiUrl).toBe(
      'https://api.example.com'
    );
  });

  it('keeps a valid half when the other parameter is invalid', () => {
    expect(readSettingsPrefill(at('?apiUrl=http://x.example&apiModel=m'))).toEqual({ apiModel: 'm' });
    expect(readSettingsPrefill(at(`?apiUrl=https://x.example&apiModel=${'m'.repeat(129)}`))).toEqual({
      apiUrl: 'https://x.example'
    });
  });

  it('does not treat the gallery model filter as a prefill', () => {
    expect(readSettingsPrefill(at('?model=gpt-image-1'))).toBeNull();
  });

  it('ignores key-like parameters', () => {
    expect(readSettingsPrefill(at('?apiKey=sk-secret'))).toBeNull();
  });
});

describe('stripSettingsPrefill', () => {
  it('removes prefill and key parameters but keeps the rest', () => {
    expect(
      stripSettingsPrefill(at('?apiUrl=https://x.example&apiModel=m&apiKey=sk&model=gallery&mode=agent#top'))
    ).toBe('/?model=gallery&mode=agent#top');
  });

  it('returns null when nothing needs stripping', () => {
    expect(stripSettingsPrefill(at('?mode=agent'))).toBeNull();
  });
});
