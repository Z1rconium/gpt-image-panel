import { describe, expect, it } from 'vitest';
import type { GalleryEntry } from '$lib/api/types/gallery';
import type { GenerateJobStatus } from '$lib/api/types/jobs';
import { initialPromptFormState, type PromptFormState } from '$lib/stores/preview';
import {
  galleryEntryToEditForm,
  galleryEntryToPromptForm,
  galleryEntryToPromptOnly,
  jobToPromptForm
} from '$lib/utils/promptForm';

function image(overrides: Partial<GalleryEntry> = {}): GalleryEntry {
  return { id: 'legacy', prompt: '', size: '', filename: 'legacy.png', favorite: false, created_at: '', ...overrides };
}

function job(overrides: Partial<GenerateJobStatus> = {}): GenerateJobStatus {
  return { job_id: 'job-1', status: 'success', ...overrides };
}

const emptyForm: PromptFormState = { ...initialPromptFormState, responseFormat: '' };

const fullParameters = {
  prompt: '  keep whitespace  ',
  api_path: '/v1/responses',
  size: '1536x1024',
  model: 'stored-model',
  quality: 'xhigh',
  output_format: 'webp',
  output_compression: 0,
  background: 'chroma_magenta',
  n: 3,
  response_format: 'b64_json'
};
const fullForm: PromptFormState = {
  prompt: '  keep whitespace  ', apiPath: '/v1/responses', size: '1536x1024', model: 'stored-model',
  quality: 'xhigh', outputFormat: 'webp', outputCompression: '0', background: 'chroma_magenta',
  quantity: 3, responseFormat: 'b64_json', stream: false, partialImages: 2, pasteBack: true
};

type NormalizedParameters = Partial<Pick<GalleryEntry, 'n' | 'quality' | 'output_format' | 'response_format' | 'background' | 'output_compression'>>;

const converters = [
  { name: 'gallery generation', convert: (values: NormalizedParameters) => galleryEntryToPromptForm(image(values)) },
  { name: 'gallery edit', convert: (values: NormalizedParameters) => galleryEntryToEditForm(image(values)) },
  { name: 'job', convert: (values: NormalizedParameters) => jobToPromptForm(job(values)) }
];

describe('prompt form conversion contracts', () => {
  it('copies full gallery parameters but edit keeps the current path and clears chroma', () => {
    const source = image(fullParameters);
    expect(galleryEntryToPromptForm(source, 'fallback', '/v1/chat/completions')).toEqual(fullForm);
    expect(galleryEntryToEditForm(source, 'fallback', '/v1/chat/completions')).toEqual({
      ...fullForm, apiPath: '/v1/chat/completions', background: 'auto'
    });
    expect(source).toEqual(image(fullParameters));
  });

  it('copies job-specific stream, partial-image and paste-back values', () => {
    expect(jobToPromptForm(job({ ...fullParameters, streaming: true, partial_images: 0, paste_back: false }))).toEqual({
      ...fullForm, stream: true, partialImages: 0, pasteBack: false
    });
    expect(jobToPromptForm(job({ streaming: null, partial_images: null, paste_back: null }))).toEqual(emptyForm);
    expect(jobToPromptForm(job({ streaming: false, partial_images: 3, paste_back: true }))).toEqual({
      ...emptyForm, partialImages: 3
    });
  });

  it('uses sparse defaults without changing the existing empty response-format default', () => {
    expect(galleryEntryToPromptForm(image())).toEqual(emptyForm);
    expect(galleryEntryToEditForm(image())).toEqual(emptyForm);
    expect(jobToPromptForm(job())).toEqual(emptyForm);
  });

  it('falls back to the supplied or initial model only for missing/empty stored models', () => {
    for (const model of [undefined, null, '']) {
      expect(galleryEntryToPromptForm(image({ model }), 'fallback').model).toBe('fallback');
      expect(galleryEntryToEditForm(image({ model }), 'fallback').model).toBe('fallback');
      expect(jobToPromptForm(job({ model }), 'fallback').model).toBe('fallback');
      expect(galleryEntryToEditForm(image({ model }), '').model).toBe(initialPromptFormState.model);
      expect(galleryEntryToPromptForm(image({ model }), '').model).toBe(initialPromptFormState.model);
      expect(jobToPromptForm(job({ model }), '').model).toBe(initialPromptFormState.model);
    }
  });

  it('keeps generation and edit API path fallback policies distinct', () => {
    for (const api_path of [undefined, null, '', '/v1/images/edits', 'invalid']) {
      expect(galleryEntryToPromptForm(image({ api_path }), 'm', '/v1/responses').apiPath).toBe('/v1/responses');
      expect(galleryEntryToEditForm(image({ api_path }), 'm', '/v1/responses').apiPath).toBe('/v1/responses');
      expect(jobToPromptForm(job({ api_path })).apiPath).toBe(initialPromptFormState.apiPath);
    }
    for (const api_path of ['/v1/images/generations', '/v1/responses', '/v1/chat/completions'] as const) {
      expect(galleryEntryToPromptForm(image({ api_path })).apiPath).toBe(api_path);
      expect(jobToPromptForm(job({ api_path })).apiPath).toBe(api_path);
    }
  });

  it('derives only edit sizes from dimensions and preserves stored sizes first', () => {
    for (const [values, size] of [
      [{ image_width: 800, image_height: 600 }, '800x600'],
      [{ image_width: 800 }, 'auto'],
      [{ image_width: 0, image_height: 600 }, 'auto'],
      [{ image_width: null, image_height: null }, 'auto'],
      [{ size: 'custom', image_width: 800, image_height: 600 }, 'custom'],
      [{ size: '-', image_width: 800, image_height: 600 }, 'auto']
    ] as [Partial<GalleryEntry>, string][]) {
      expect(galleryEntryToEditForm(image(values)).size).toBe(size);
      expect(galleryEntryToPromptForm(image(values)).size).toBe(values.size || 'auto');
    }
  });

  it('prompt-only reuse preserves every current setting and does not mutate it', () => {
    const current: PromptFormState = { ...fullForm, stream: true, partialImages: 1, pasteBack: false };
    expect(galleryEntryToPromptOnly(image({ prompt: 'new' }), current)).toEqual({ ...current, prompt: 'new' });
    expect(galleryEntryToPromptOnly(image(), current)).toEqual({ ...current, prompt: '' });
    expect(current).toEqual({ ...fullForm, stream: true, partialImages: 1, pasteBack: false });
  });
});

for (const { name, convert } of converters) {
  describe(`${name} normalization`, () => {
    it.each([
      [undefined, 1], [null, 1], [0, 1], [-4, 1], [1, 1], [10, 10], [50, 10], [2.5, 2.5], [NaN, 1]
    ])('keeps quantity %s as %s', (n, expected) => {
      expect(convert({ n }).quantity).toBe(expected);
    });

    it.each([[undefined, ''], [null, ''], [0, '0'], [85, '85']])('keeps compression %s as %s', (output_compression, expected) => {
      expect(convert({ output_compression }).outputCompression).toBe(expected);
    });

    it.each(['auto', 'low', 'medium', 'high', 'xhigh', 'max', 'invalid', '', null, undefined])('normalizes quality %s', (quality) => {
      expect(convert({ quality }).quality).toBe(['auto', 'low', 'medium', 'high', 'xhigh', 'max'].includes(quality || '') ? quality : 'auto');
    });

    it.each(['png', 'jpeg', 'webp', 'invalid', '', null, undefined])('normalizes output format %s', (output_format) => {
      expect(convert({ output_format }).outputFormat).toBe(['png', 'jpeg', 'webp'].includes(output_format || '') ? output_format : 'png');
    });

    it.each(['', 'url', 'b64_json', 'invalid', null, undefined])('normalizes response format %s', (response_format) => {
      expect(convert({ response_format }).responseFormat).toBe(response_format === 'url' || response_format === 'b64_json' ? response_format : '');
    });

    it.each(['auto', 'opaque', 'transparent', 'chroma_green', 'chroma_magenta', 'chroma_unknown', 'invalid', '', null, undefined])('normalizes background %s', (background) => {
      const valid = ['opaque', 'transparent', ...(name === 'gallery edit' ? [] : ['chroma_green', 'chroma_magenta'])];
      expect(convert({ background }).background).toBe(valid.includes(background || '') ? background : 'auto');
    });
  });
}
