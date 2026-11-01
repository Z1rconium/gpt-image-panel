import type { PresetHealthStatus } from '$lib/api/types/common';
import { t } from '$lib/i18n';
import { get } from 'svelte/store';

export function healthPanelClass(status: PresetHealthStatus): string {
  if (status === 'ok') return 'border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-500/40 dark:bg-emerald-500/10 dark:text-emerald-100';
  if (status === 'warning') return 'border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-100';
  return 'border-red-200 bg-red-50 text-red-800 dark:border-red-500/40 dark:bg-red-500/10 dark:text-red-100';
}

export function healthBadgeClass(status: PresetHealthStatus): string {
  if (status === 'ok') return 'border-emerald-300 text-emerald-700 dark:border-emerald-500/40 dark:text-emerald-300';
  if (status === 'warning') return 'border-amber-300 text-amber-700 dark:border-amber-500/40 dark:text-amber-300';
  return 'border-red-300 text-red-700 dark:border-red-500/40 dark:text-red-300';
}

export function healthStatusLabel(status: PresetHealthStatus): string {
  const messages = get(t);
  if (status === 'ok') return messages.settings.healthOk;
  if (status === 'warning') return messages.settings.healthWarning;
  return messages.settings.healthError;
}
