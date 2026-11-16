import { describe, expect, it } from 'vitest';
import type { ApiPath } from '$lib/api/types/common';
import type { GenerateRequestBody } from '$lib/api/types/generation';
import type { ProviderCapabilities } from '$lib/api/types/settings';
import { initialPromptFormState, type PromptFormState } from '$lib/utils/promptFormTypes';
import {
  buildRequestBody,
  normalizeForm,
  validateSubmission,
  type NormalizeFormContext,
  type SubmissionErrorKey
} from '$lib/utils/promptRules';

function makeForm(overrides: Partial<PromptFormState> = {}): PromptFormState {
  return {
    ...initialPromptFormState,
    prompt: 'a crisp red apple',
    ...overrides
  };
}

const RESTRICTED_CAPS: ProviderCapabilities = {
  generate: true,
  edit: true,
  mask: false,
  stream: false,
  transparent_background: false,
  markup: false,
  complexity: false,
  animation: false,
  formats: ['png', 'jpeg', 'webp']
};

const FULL_26_CAPS: ProviderCapabilities = {
  generate: true,
  edit: true,
  mask: true,
  stream: true,
  transparent_background: true,
  markup: true,
  complexity: true,
  animation: true,
  formats: ['png', 'jpeg', 'webp', 'svg', 'gif']
};

describe('buildRequestBody table-driven contract', () => {
  const cases: {
    name: string;
    form: Partial<PromptFormState>;
    expected: Partial<GenerateRequestBody>;
    omitted?: (keyof GenerateRequestBody)[];
  }[] = [
    {
      name: 'legacy model preserves response_format and trims strings',
      form: {
        prompt: '  clouds  ',
        model: '  gpt-image-2  ',
        responseFormat: 'b64_json',
        safetyIdentifier: '  user-1  '
      },
      expected: {
        prompt: 'clouds',
        model: 'gpt-image-2',
        response_format: 'b64_json',
        safety_identifier: 'user-1',
        output_compression: null
      }
    },
    {
      name: '2.5 model sets response_format to null and keeps api_path',
      form: {
        model: 'gpt-image-2.5-flare',
        quality: 'max',
        responseFormat: 'url',
        apiPath: '/v1/responses'
      },
      expected: {
        model: 'gpt-image-2.5-flare',
        quality: 'max',
        response_format: null,
        api_path: '/v1/responses'
      }
    },
    {
      name: '2.6 model deletes response_format, forces generations path, and forwards complexity',
      form: {
        model: 'gpt-image-2.6-flare',
        apiPath: '/v1/responses',
        complexity: 'high',
        responseFormat: 'url'
      },
      expected: {
        model: 'gpt-image-2.6-flare',
        api_path: '/v1/images/generations',
        complexity: 'high'
      },
      omitted: ['response_format', 'frame_count', 'frame_duration_ms']
    },
    {
      name: '2.6 gif clears stream/compression/chroma, keeps n, and includes frame controls',
      form: {
        model: 'gpt-image-2.6-sunburst-2026-11-04',
        outputFormat: 'gif',
        background: 'chroma_green',
        outputCompression: '75',
        stream: true,
        quantity: 3,
        frameCount: 12,
        frameDurationMs: 150
      },
      expected: {
        output_format: 'gif',
        background: 'auto',
        output_compression: null,
        n: 3,
        frame_count: 12,
        frame_duration_ms: 150
      },
      omitted: ['stream', 'partial_images', 'response_format']
    },
    {
      name: '2.6 svg forces n=1 and clears stream/compression/chroma',
      form: {
        model: 'gpt-image-2.6-flare',
        outputFormat: 'svg',
        background: 'chroma_magenta',
        outputCompression: '50',
        stream: true,
        quantity: 4
      },
      expected: {
        output_format: 'svg',
        background: 'auto',
        output_compression: null,
        n: 1
      },
      omitted: ['stream', 'partial_images', 'response_format']
    },
    {
      name: 'chroma background forces png and drops compression for raster formats',
      form: {
        outputFormat: 'webp',
        outputCompression: '85',
        background: 'chroma_green'
      },
      expected: {
        output_format: 'png',
        output_compression: null,
        background: 'chroma_green'
      }
    },
    {
      name: 'transparent + jpeg forces png and drops compression',
      form: {
        outputFormat: 'jpeg',
        outputCompression: '85',
        background: 'transparent'
      },
      expected: {
        output_format: 'png',
        output_compression: null,
        background: 'transparent'
      }
    },
    {
      name: 'transparent + webp keeps webp and clamps compression',
      form: {
        outputFormat: 'webp',
        outputCompression: '150',
        background: 'transparent'
      },
      expected: {
        output_format: 'webp',
        output_compression: 100,
        background: 'transparent'
      }
    },
    {
      name: 'stream clamps partial_images and quantity clamps to [1, 10]',
      form: {
        quantity: 99,
        stream: true,
        partialImages: 7
      },
      expected: {
        n: 10,
        stream: true,
        partial_images: 3
      }
    },
    {
      name: 'empty quantity defaults to 1 and blank safetyIdentifier is omitted',
      form: {
        quantity: '',
        safetyIdentifier: '   '
      },
      expected: {
        n: 1
      },
      omitted: ['safety_identifier']
    }
  ];

  it.each(cases)('$name', ({ form, expected, omitted }) => {
    const body = buildRequestBody(makeForm(form));
    expect(body).toMatchObject(expected);
    for (const key of omitted ?? []) {
      expect(body).not.toHaveProperty(key);
    }
  });
});

describe('validateSubmission table-driven contract', () => {
  const cases: {
    name: string;
    form?: Partial<PromptFormState>;
    bodyOverrides?: Partial<GenerateRequestBody>;
    edit?: boolean;
    hasMask?: boolean;
    expected: SubmissionErrorKey | null;
  }[] = [
    { name: 'valid default generation', expected: null },
    {
      name: 'empty prompt rejected',
      form: { prompt: '   ' },
      expected: 'prompt_required'
    },
    {
      name: '32000 code points allowed',
      form: { prompt: '🐈'.repeat(32000) },
      expected: null
    },
    {
      name: '32001 code points rejected',
      form: { prompt: '🐈'.repeat(32001) },
      expected: 'prompt_too_long'
    },
    {
      name: 'svg on non-2.6 rejected as media_reset',
      form: { model: 'gpt-image-2.5-flare', outputFormat: 'svg' },
      expected: 'media_reset'
    },
    {
      name: 'gif on legacy model rejected as media_reset',
      form: { model: 'gpt-image-2', outputFormat: 'gif' },
      expected: 'media_reset'
    },
    {
      name: 'svg edit rejected as media_reset',
      form: { model: 'gpt-image-2.6-flare', outputFormat: 'svg' },
      edit: true,
      expected: 'media_reset'
    },
    {
      name: 'gif edit without mask allowed',
      form: { model: 'gpt-image-2.6-flare', outputFormat: 'gif', size: '64x64' },
      edit: true,
      hasMask: false,
      expected: null
    },
    {
      name: 'gif edit with mask rejected as media_reset',
      form: { model: 'gpt-image-2.6-flare', outputFormat: 'gif', size: '64x64' },
      edit: true,
      hasMask: true,
      expected: 'media_reset'
    },
    {
      name: 'gif frame_count below 2 rejected',
      form: { model: 'gpt-image-2.6-flare', outputFormat: 'gif', frameCount: 1 },
      expected: 'invalid_frames'
    },
    {
      name: 'gif frame_count above 64 rejected',
      form: { model: 'gpt-image-2.6-flare', outputFormat: 'gif', frameCount: 65 },
      expected: 'invalid_frames'
    },
    {
      name: 'gif frame_duration_ms not multiple of 10 rejected',
      form: { model: 'gpt-image-2.6-flare', outputFormat: 'gif', frameDurationMs: 25 },
      expected: 'invalid_frames'
    },
    {
      name: 'gif frame boundaries 2 frames and 2000ms allowed',
      form: { model: 'gpt-image-2.6-flare', outputFormat: 'gif', frameCount: 2, frameDurationMs: 2000 },
      expected: null
    },
    {
      name: '2.5 on responses endpoint blocked for generation',
      form: { model: 'gpt-image-2.5-flare', apiPath: '/v1/responses' },
      expected: 'image25_endpoint'
    },
    {
      name: '2.5 on responses endpoint allowed for edits',
      form: { model: 'gpt-image-2.5-flare', apiPath: '/v1/responses' },
      edit: true,
      expected: null
    },
    {
      name: '2.6 raw body on responses endpoint rejected if passed directly',
      bodyOverrides: { model: 'gpt-image-2.6-flare', api_path: '/v1/responses' },
      expected: 'image25_endpoint'
    },
    {
      name: 'xhigh quality rejected on legacy model',
      form: { model: 'gpt-image-2', quality: 'xhigh' },
      expected: 'quality_reset'
    },
    {
      name: 'xhigh quality allowed on 2.5 and 2.6',
      form: { model: 'gpt-image-2.5-sunburst', quality: 'xhigh' },
      expected: null
    },
    {
      name: 'small size rejected for png generation',
      form: { size: '64x64', outputFormat: 'png' },
      expected: 'invalid_size'
    },
    {
      name: 'small size allowed for 2.6 gif',
      form: { model: 'gpt-image-2.6-flare', outputFormat: 'gif', size: '64x64' },
      expected: null
    },
    {
      name: 'stream on chat completions rejected for generation',
      form: { apiPath: '/v1/chat/completions', stream: true },
      expected: 'stream_unsupported_path'
    },
    {
      name: 'stream on chat completions allowed for edits',
      form: { apiPath: '/v1/chat/completions', stream: true },
      edit: true,
      expected: null
    },
    {
      name: 'chroma on responses rejected for generation',
      form: { apiPath: '/v1/responses', background: 'chroma_green' },
      expected: 'chroma_unsupported_path'
    }
  ];

  it.each(cases)('$name', ({ form, bodyOverrides, edit, hasMask, expected }) => {
    const body = { ...buildRequestBody(makeForm(form)), ...bodyOverrides };
    expect(validateSubmission(body, { edit, hasMask })).toBe(expected);
  });
});

describe('normalizeForm rules and resolved ordering', () => {
  it('clears complexity when mapped provider does not support complexity', () => {
    const { form, resets } = normalizeForm(makeForm({ model: 'gpt-image-2.6-flare', complexity: 'high' }), {
      providerMapped: true,
      providerCaps: RESTRICTED_CAPS,
      editMode: false,
      hasMask: false
    });
    expect(form.complexity).toBeNull();
    expect(resets).toContain('complexity_reset');
  });

  it('checks provider svg/gif support before special-media side-effects so stream and quantity are preserved', () => {
    const { form, resets } = normalizeForm(
      makeForm({
        model: 'gpt-image-2.6-flare',
        outputFormat: 'svg',
        stream: true,
        quantity: 3,
        outputCompression: '80'
      }),
      {
        providerMapped: true,
        providerCaps: { ...FULL_26_CAPS, formats: ['png', 'jpeg', 'webp'] },
        editMode: false,
        hasMask: false
      }
    );
    expect(form.outputFormat).toBe('png');
    expect(form.stream).toBe(true);
    expect(form.quantity).toBe(3);
    expect(form.outputCompression).toBe('');
    expect(resets).toEqual(['media_reset', 'compression_reset']);
  });

  it('turns chroma into auto and preserves gif when 2.6 gif is valid', () => {
    const { form, resets } = normalizeForm(
      makeForm({
        model: 'gpt-image-2.6-flare',
        outputFormat: 'gif',
        background: 'chroma_green',
        stream: true,
        outputCompression: '40'
      }),
      { editMode: false, hasMask: false }
    );
    expect(form.outputFormat).toBe('gif');
    expect(form.background).toBe('auto');
    expect(form.stream).toBe(false);
    expect(form.outputCompression).toBe('');
    expect(resets).toEqual(['media_reset']);
  });

  it('resets unsupported transparent background before transparent+jpeg->png so jpeg is preserved', () => {
    const { form, resets } = normalizeForm(
      makeForm({
        background: 'transparent',
        outputFormat: 'jpeg',
        outputCompression: '75'
      }),
      {
        providerMapped: true,
        providerCaps: RESTRICTED_CAPS,
        editMode: false,
        hasMask: false
      }
    );
    expect(form.background).toBe('auto');
    expect(form.outputFormat).toBe('jpeg');
    expect(form.outputCompression).toBe('75');
    expect(resets).toEqual(['transparent_unsupported']);
  });

  it('converts transparent+jpeg to png and clears compression when transparency is supported', () => {
    const { form, resets } = normalizeForm(
      makeForm({
        background: 'transparent',
        outputFormat: 'jpeg',
        outputCompression: '75'
      }),
      { editMode: false, hasMask: false }
    );
    expect(form.background).toBe('transparent');
    expect(form.outputFormat).toBe('png');
    expect(form.outputCompression).toBe('');
    expect(resets).toEqual(['transparent_jpeg_to_png']);
  });

  it('resets chroma to auto in edit or prompt-only mode and forces png in generation mode', () => {
    const editRes = normalizeForm(
      makeForm({ background: 'chroma_magenta', outputFormat: 'webp', outputCompression: '60' }),
      { editMode: true, hasMask: false }
    );
    expect(editRes.form.background).toBe('auto');
    expect(editRes.form.outputFormat).toBe('webp');
    expect(editRes.form.outputCompression).toBe('60');
    expect(editRes.resets).toEqual(['chroma_reset']);

    const genRes = normalizeForm(
      makeForm({ background: 'chroma_magenta', outputFormat: 'webp', outputCompression: '60' }),
      { editMode: false, hasMask: false }
    );
    expect(genRes.form.background).toBe('chroma_magenta');
    expect(genRes.form.outputFormat).toBe('png');
    expect(genRes.form.outputCompression).toBe('');
    expect(genRes.resets).toEqual(['chroma_to_png']);
  });

  it('is idempotent and produces submittable forms across model x format x background x stream x context', () => {
    const models = ['gpt-image-2', 'gpt-image-2.5-flare', 'gpt-image-2.6-flare'];
    const apiPaths: ApiPath[] = ['/v1/images/generations', '/v1/responses', '/v1/chat/completions'];
    const formats: PromptFormState['outputFormat'][] = ['png', 'jpeg', 'webp', 'svg', 'gif'];
    const backgrounds: PromptFormState['background'][] = [
      'auto',
      'opaque',
      'transparent',
      'chroma_green',
      'chroma_magenta'
    ];
    const qualities: PromptFormState['quality'][] = ['auto', 'high', 'xhigh', 'max'];
    const contexts: NormalizeFormContext[] = [
      { editMode: false, hasMask: false },
      { editMode: true, hasMask: false },
      { editMode: true, hasMask: true },
      { providerMapped: true, providerCaps: RESTRICTED_CAPS, editMode: false, hasMask: false },
      { providerMapped: true, providerCaps: FULL_26_CAPS, editMode: true, hasMask: false }
    ];

    for (const ctx of contexts) {
      for (const model of models) {
        for (const apiPath of apiPaths) {
          for (const outputFormat of formats) {
            for (const background of backgrounds) {
              for (const quality of qualities) {
                for (const stream of [false, true]) {
                  const raw = makeForm({
                    model,
                    apiPath,
                    outputFormat,
                    background,
                    quality,
                    stream,
                    outputCompression: '55',
                    quantity: 3,
                    complexity: 'low'
                  });
                  const first = normalizeForm(raw, ctx);
                  const second = normalizeForm(first.form, ctx);
                  expect(second.form).toEqual(first.form);
                  expect(second.resets).toEqual([]);

                  // On the API-path level, 2.5 generation on non-/v1/images/generations is
                  // intentionally validated on submit rather than auto-rewritten by the form.
                  const isBlocked25Endpoint =
                    !ctx.editMode && model.startsWith('gpt-image-2.5') && apiPath !== '/v1/images/generations';
                  if (!isBlocked25Endpoint) {
                    expect(
                      validateSubmission(buildRequestBody(first.form), {
                        edit: ctx.editMode,
                        hasMask: ctx.hasMask
                      })
                    ).toBeNull();
                  }
                }
              }
            }
          }
        }
      }
    }
  });
});
