import { untrack } from 'svelte';
import type { ApiPath, ResponseFormatDefault } from '$lib/api/types/common';
import type { SettingsResponse } from '$lib/api/types/settings';
import { initialPromptFormState, type PromptFormState } from '$lib/stores/preview';
import {
  activePreset,
  presetApiPath,
  presetDefaultModel,
  presetDefaultResponseFormat
} from '$lib/features/workspace/prompt';
import { normalizeSubmissionQuantity } from '$lib/utils/promptForm';

/**
 * Field-level reactive prompt form state shared across the workspace.
 * Per-field $state keeps a prompt keystroke from invalidating consumers that
 * only read other fields (assistant panels, optimizer, size dialog), which
 * the previous whole-object `bind:form` bridge re-rendered on every keypress.
 */
class PromptFormController {
  prompt = $state(initialPromptFormState.prompt);
  apiPath = $state<ApiPath>(initialPromptFormState.apiPath);
  size = $state(initialPromptFormState.size);
  model = $state(initialPromptFormState.model);
  quality = $state<PromptFormState['quality']>(initialPromptFormState.quality);
  outputFormat = $state<PromptFormState['outputFormat']>(initialPromptFormState.outputFormat);
  background = $state<PromptFormState['background']>(initialPromptFormState.background);
  outputCompression = $state(initialPromptFormState.outputCompression);
  quantity = $state<number | string>(initialPromptFormState.quantity);
  responseFormat = $state<ResponseFormatDefault>(initialPromptFormState.responseFormat);
  stream = $state(initialPromptFormState.stream);
  partialImages = $state(initialPromptFormState.partialImages);
  pasteBack = $state(typeof localStorage === 'undefined' ? true : localStorage.getItem('maskEditor.pasteBack') !== 'false');

  #lastPresetId = '';
  #presetDefaultModel = initialPromptFormState.model;
  #presetApiPath: ApiPath = initialPromptFormState.apiPath;
  #presetDefaultResponseFormat: ResponseFormatDefault = initialPromptFormState.responseFormat;

  /** Default model of the active preset; the fallback for loaded jobs/gallery params. */
  get presetDefaultModel(): string {
    return this.#presetDefaultModel;
  }

  /** Plain-object snapshot in the shape the submission path consumes. */
  snapshot(): PromptFormState {
    return {
      prompt: this.prompt,
      apiPath: this.apiPath,
      size: this.size,
      model: this.model,
      quality: this.quality,
      outputFormat: this.outputFormat,
      background: this.background,
      outputCompression: this.outputCompression,
      quantity: this.quantity,
      responseFormat: this.responseFormat,
      stream: this.stream,
      partialImages: this.partialImages,
      pasteBack: this.pasteBack
    };
  }

  patch(updates: Partial<PromptFormState>) {
    if (updates.prompt !== undefined) this.prompt = updates.prompt;
    if (updates.apiPath !== undefined) this.apiPath = updates.apiPath;
    if (updates.size !== undefined) this.size = updates.size;
    if (updates.model !== undefined) this.model = updates.model;
    if (updates.quality !== undefined) this.quality = updates.quality;
    if (updates.outputFormat !== undefined) this.outputFormat = updates.outputFormat;
    if (updates.background !== undefined) this.background = updates.background;
    if (updates.outputCompression !== undefined) this.outputCompression = updates.outputCompression;
    if (updates.quantity !== undefined) this.quantity = updates.quantity;
    if (updates.responseFormat !== undefined) this.responseFormat = updates.responseFormat;
    if (updates.stream !== undefined) this.stream = updates.stream;
    if (updates.partialImages !== undefined) this.partialImages = updates.partialImages;
    if (updates.pasteBack !== undefined) this.pasteBack = updates.pasteBack;
  }

  replace(next: PromptFormState) {
    this.patch(next);
  }

  reset() {
    this.patch({ ...initialPromptFormState, pasteBack: this.pasteBack });
  }

  /** Clamp the free-text quantity input to the submittable range. */
  normalizeQuantityForSubmit() {
    const quantity = normalizeSubmissionQuantity(this.quantity);
    if (this.quantity === '' || Number(this.quantity) !== quantity) {
      this.quantity = quantity;
    }
  }

  /**
   * Follow the active preset's defaults (model/api path/response format) while
   * respecting values the user has explicitly chosen. Untracked so callers can
   * run it from an effect that should only depend on the settings object.
   */
  applyPresetDefaults(settings: SettingsResponse | null) {
    untrack(() => {
      const preset = activePreset(settings);
      const nextPresetId = preset?.id || '';
      const nextDefaultModel = presetDefaultModel(settings);
      const nextApiPath = presetApiPath(settings);
      const nextDefaultResponseFormat = presetDefaultResponseFormat(settings);
      if (
        nextPresetId === this.#lastPresetId &&
        nextDefaultModel === this.#presetDefaultModel &&
        nextApiPath === this.#presetApiPath &&
        nextDefaultResponseFormat === this.#presetDefaultResponseFormat
      ) {
        return;
      }

      const currentModel = this.model.trim();
      if (!currentModel || currentModel === this.#presetDefaultModel) {
        this.model = nextDefaultModel;
      }
      if (!this.apiPath || this.apiPath === this.#presetApiPath) {
        this.apiPath = nextApiPath;
      }
      if (nextPresetId !== this.#lastPresetId || nextDefaultResponseFormat !== this.#presetDefaultResponseFormat) {
        this.responseFormat = nextDefaultResponseFormat;
      }
      this.#lastPresetId = nextPresetId;
      this.#presetDefaultModel = nextDefaultModel;
      this.#presetApiPath = nextApiPath;
      this.#presetDefaultResponseFormat = nextDefaultResponseFormat;
    });
  }
}

export const promptForm = new PromptFormController();
