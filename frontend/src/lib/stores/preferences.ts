import { writable } from 'svelte/store';
import { sanitizeNamedSizePresets, type NamedSizePreset } from '$lib/utils/sizePresets';

/**
 * Per-browser workspace habits. They are UI conveniences rather than server
 * config (there are no per-user accounts), so they live in localStorage like
 * the mask editor preferences and survive storage being unavailable.
 */
export type WorkspacePreferences = {
  clearPromptAfterSubmit: boolean;
  retryUsesJobPreset: boolean;
  sizePresets: NamedSizePreset[];
};

export const PREFERENCES_STORAGE_KEY = 'gpt-image-panel-preferences';
const PREFERENCES_VERSION = 1;

export const defaultPreferences: WorkspacePreferences = {
  clearPromptAfterSubmit: false,
  retryUsesJobPreset: false,
  sizePresets: []
};

function readPreferences(): WorkspacePreferences {
  try {
    if (typeof localStorage === 'undefined') return defaultPreferences;
    const raw = localStorage.getItem(PREFERENCES_STORAGE_KEY);
    if (!raw) return defaultPreferences;
    const parsed = JSON.parse(raw) as Partial<WorkspacePreferences> & { version?: number };
    if (!parsed || typeof parsed !== 'object') return defaultPreferences;
    return {
      clearPromptAfterSubmit: parsed.clearPromptAfterSubmit === true,
      retryUsesJobPreset: parsed.retryUsesJobPreset === true,
      sizePresets: sanitizeNamedSizePresets(parsed.sizePresets)
    };
  } catch {
    return defaultPreferences;
  }
}

function writePreferences(preferences: WorkspacePreferences) {
  try {
    if (typeof localStorage === 'undefined') return;
    localStorage.setItem(PREFERENCES_STORAGE_KEY, JSON.stringify({ version: PREFERENCES_VERSION, ...preferences }));
  } catch {
    // Storage may be full or disabled; the in-memory value still applies.
  }
}

function createPreferencesStore() {
  const { subscribe, update } = writable<WorkspacePreferences>(readPreferences());

  function patch(updates: Partial<WorkspacePreferences>) {
    update((current) => {
      const next = { ...current, ...updates };
      writePreferences(next);
      return next;
    });
  }

  return { subscribe, patch };
}

export const preferencesStore = createPreferencesStore();
