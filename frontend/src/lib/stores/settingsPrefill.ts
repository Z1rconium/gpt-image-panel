import { writable } from 'svelte/store';
import type { SettingsPrefill } from '$lib/utils/settingsPrefill';

// Holds a link-provided preset suggestion until the user applies or dismisses it.
export const settingsPrefillStore = writable<SettingsPrefill | null>(null);
