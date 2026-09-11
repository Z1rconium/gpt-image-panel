import { get, writable } from 'svelte/store';
import { t } from '$lib/i18n';
import { uiStore, type ToastOptions, type ToastVariant } from '$lib/stores/ui';
import { canPrefetchNonCritical } from '$lib/utils/network';
import { lazyPanels, type LazyPanel } from '$lib/features/workspace/panels';

export type PanelUiKey =
  | 'settingsOpen'
  | 'jobsOpen'
  | 'promptSnippetsOpen'
  | 'imagePromptOpen'
  | 'sizeDialogOpen'
  | 'editPreviewOpen';

type ShowToast = (message: string, variant?: ToastVariant, options?: ToastOptions) => void;

/**
 * Owns lazy-panel loading, retry, and focus restoration. Workspace keeps the
 * business state; this controller only coordinates the dynamic-import boundary
 * and the focus handoff around it.
 */
export function createPanelController(showToast: ShowToast) {
  const loadingPanel = writable<LazyPanel | null>(null);
  let sequence = 0;
  const focusTargets: Partial<Record<LazyPanel, HTMLElement>> = {};

  async function ensurePanel(panel: LazyPanel, showLoading = true, onRetry?: () => void) {
    const currentSequence = ++sequence;
    if (showLoading) loadingPanel.set(panel);
    try {
      await lazyPanels[panel].load();
      return true;
    } catch {
      const reloadRequired = lazyPanels[panel].retryRequiresReload();
      lazyPanels[panel].reset();
      showToast(get(t).common.loadFeatureFailed, 'error', {
        actionLabel: get(t).common.retry,
        onAction: reloadRequired ? () => window.location.reload() : onRetry || (() => void ensurePanel(panel))
      });
      return false;
    } finally {
      if (currentSequence === sequence) loadingPanel.set(null);
    }
  }

  function prefetchPanel(panel: LazyPanel) {
    if (!canPrefetchNonCritical()) return;
    void lazyPanels[panel].prefetch();
  }

  function rememberPanelFocus(panel: LazyPanel) {
    if (focusTargets[panel] || typeof document === 'undefined') return;
    const active = document.activeElement;
    if (active instanceof HTMLElement) focusTargets[panel] = active;
  }

  function restorePanelFocus(panel: LazyPanel) {
    const target = focusTargets[panel];
    delete focusTargets[panel];
    queueMicrotask(() => {
      if (target?.isConnected) target.focus();
    });
  }

  async function openPanel(panel: LazyPanel, key: PanelUiKey) {
    rememberPanelFocus(panel);
    if (await ensurePanel(panel, true, () => void openPanel(panel, key))) uiStore.setKey(key, true);
  }

  function closePanel(panel: LazyPanel, key: PanelUiKey) {
    uiStore.setKey(key, false);
    restorePanelFocus(panel);
  }

  function reset() {
    sequence = 0;
    loadingPanel.set(null);
    for (const key of Object.keys(focusTargets) as LazyPanel[]) delete focusTargets[key];
  }

  return {
    loadingPanel,
    ensurePanel,
    prefetchPanel,
    rememberPanelFocus,
    restorePanelFocus,
    openPanel,
    closePanel,
    reset
  };
}

export type PanelController = ReturnType<typeof createPanelController>;
